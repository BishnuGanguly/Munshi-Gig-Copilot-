"""The pipeline's understanding and wording, tested against a fake provider that speaks our own interface.
The tests pin down what the code does with whatever a model returns - including output that invents numbers.
Vendor wire formats are tested separately (tests/test_llm_*.py)."""
import base64
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core import brain, dates, db, tools
from app.core.actions import REGISTRY
from app.core.brain import Renderer, Understander, preprocess, speakable
from app.errors import LlmUnavailableError
from app.main import create_app
from tests.audio_helpers import make_audio
from tests.fakes import FakeProvider, rendering

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)


def router_for(calls):
    provider = FakeProvider(turns=[calls])
    return Understander(REGISTRY, provider), provider


def renderer_for(language="hi-IN", reply="theek hai"):
    provider = FakeProvider(text=rendering(language, reply))
    return Renderer(provider), provider


@pytest.fixture
def chat():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")

    def say(text, **kw):
        return brain.handle(conn, "u", text, NOW, **kw)

    say.conn = conn
    return say


def saved(chat):
    return [dict(r) for r in chat.conn.execute("SELECT type, amount, category, platform FROM transactions")]


# ---------------------------------------------------------------- what we deliberately keep in code
@pytest.mark.parametrize("raw,expected", [
    ("आज मैं दो सौ पचास रुपए का बचत किया", "आज मैं 250 रुपए का बचत किया"),
    ("zomato se dedh hazaar mile", "Zomato se 1500 mile"),
    ("saadhe teen sau kharcha", "350 kharcha"),
    ("१४०० आए", "1400 आए"),
    ("इन்று 1400 கிடைத்தது", "इन்று 1400 கிடைத்தது"),
])
def test_amounts_become_digits_before_the_model_sees_them(raw, expected):
    """Arithmetic stays in code: deterministic, free, and it is what lets the number guard work."""
    assert preprocess(raw) == expected


def test_confirmations_never_reach_the_model(chat):
    r, api = router_for([("log_entries", {"entries": [{"type": "income", "amount": 500}]})])
    chat("500 aaya", understander=r)
    calls_before = len(api.chooses)
    out = chat("haan theek hai", understander=r)                     # yes, in Hinglish
    assert out.outcome.reply.startswith("Saved")
    assert len(api.chooses) == calls_before                   # no round-trip for the commonest message
    assert saved(chat)[0]["amount"] == 500


def test_no_discards_locally(chat):
    r, api = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 250, "category": "fuel"}]})])
    chat("250 petrol", understander=r)
    n = len(api.chooses)
    assert "discarded" in chat("nahi", understander=r).outcome.reply
    assert len(api.chooses) == n and saved(chat) == []


def test_yes_with_nothing_pending(chat):
    r, api = router_for([])
    assert chat("yes", understander=r).outcome.reply.startswith("There is nothing")
    assert api.requests == []


# ---------------------------------------------------------------- the model understands, our code executes
def test_regional_language_logging_end_to_end(chat):
    r, _ = router_for([("log_entries", {"entries": [{"type": "income", "amount": 1400, "platform": "Swiggy"}]})])
    rend, rapi = renderer_for("ta-IN", "இன்று ₹1,400 வரவு Swiggy. சரியா?")
    out = chat("இன்று ஸ்விக்கியில் 1400 ரூபாய் கிடைத்தது", understander=r, renderer=rend)
    assert "Please confirm: income ₹1,400 from Swiggy" in out.outcome.reply      # our English fact
    assert out.reply_user == "இன்று ₹1,400 வரவு Swiggy. சரியா?"                  # rendered for the rider
    assert out.reply_language == "ta-IN" and saved(chat) == []                   # still unconfirmed
    assert "இன்று ஸ்விக்கியில் 1400" in rapi.completes[0]["user"]


def test_no_translation_call_is_made(chat):
    """The rider's words go to the model as they are - there is no to_english hop any more."""
    r, api = router_for([("query_summary", {"kind": "earnings", "period": "this_week"})])
    chat("इस हफ़्ते कितना कमाया", understander=r, renderer=None)
    sent = api.chooses[0]["user"]
    assert "इस हफ़्ते कितना कमाया" in sent


def test_a_correction_while_pending_replaces_it(chat):
    r1, _ = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 250, "category": "fuel"}]})])
    chat("250 ka petrol", understander=r1)
    r2, api2 = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 300, "category": "fuel"}]})])
    out = chat("250 nahi 300 tha", understander=r2)
    assert "₹300" in out.outcome.reply
    assert "An action is already waiting" in api2.chooses[0]["user"]   # the model is told
    chat("haan")
    assert saved(chat)[0]["amount"] == 300


def test_target_flow(chat):
    r, _ = router_for([("set_target", {"amount": 1500})])
    assert "₹1,500" in chat("roz 1500 kamana hai", understander=r).outcome.reply
    chat("haan")
    assert tools.get_target(chat.conn, "u", NOW.date()) == 1500


def test_invented_amounts_are_still_dropped(chat):
    r, _ = router_for([("log_entries", {"entries": [{"type": "income", "amount": 9999}]})])
    out = chat("Swiggy ka paisa aaya", understander=r)
    assert "9,999" not in out.outcome.reply and saved(chat) == []


def test_without_a_model_it_says_so_rather_than_guessing(chat):
    out = chat("कुछ भी", understander=None, renderer=None)
    assert out.outcome.intent == "unknown" and "understanding_unavailable" in out.warnings


# ---------------------------------------------------------------- the renderer, and the number guard
def test_render_asks_for_a_rewording_and_nothing_else():
    rend, provider = renderer_for()
    rend.render("Saved. Today's profit is ₹350.", "250 ka petrol")
    req = provider.requests[0]
    assert req["kind"] == "complete" and "tools" not in req          # rewording is not a tool call
    assert req["system"] == brain.SYSTEM_RENDER and "SAME" in req["system"]
    assert "250 ka petrol" in req["user"] and "Saved. Today's profit is ₹350." in req["user"]


def test_render_cannot_invent_a_number():
    """The whole point: the model words our sentence, it does not state figures."""
    rend, _ = renderer_for("hi-IN", "आज का मुनाफ़ा ₹9,999 है")
    reply, lang, warnings = rend.render("Saved. Today's profit is ₹350.", "250 ka petrol")
    assert reply == "Saved. Today's profit is ₹350."            # English kept
    assert lang == "en-IN" and warnings[0].startswith("reply_numbers_invented")


def test_render_may_keep_our_numbers():
    rend, _ = renderer_for("hi-IN", "सेव हो गया। आज का मुनाफ़ा ₹350 है।")
    reply, lang, warnings = rend.render("Saved. Today's profit is ₹350.", "250 ka petrol")
    assert reply == "सेव हो गया। आज का मुनाफ़ा ₹350 है।" and lang == "hi-IN" and warnings == []


def test_render_normalises_native_digits():
    rend, _ = renderer_for("hi-IN", "आज का मुनाफ़ा ₹३५० है")
    assert rend.render("Today's profit is ₹350.", "x")[0] == "आज का मुनाफ़ा ₹350 है"


@pytest.mark.parametrize("text", ["not json", '{"language": "hi-IN"}', '{"language": "hi-IN", "reply": ""}', ""])
def test_render_bad_output_keeps_the_english_reply(text):
    reply, lang, warnings = Renderer(FakeProvider(text=text)).render("Saved.", "x")
    assert reply == "Saved." and lang == "en-IN" and warnings == ["reply_not_rendered"]


@pytest.mark.parametrize("error", [LlmUnavailableError("down"), RuntimeError("connection reset"), ValueError("x")])
def test_render_provider_failure_keeps_the_english_reply(error):
    reply, lang, warnings = Renderer(FakeProvider(raises=error)).render("Saved.", "x")
    assert reply == "Saved." and lang == "en-IN" and warnings == ["reply_not_rendered"]


@pytest.mark.parametrize("error", [LlmUnavailableError("down"), RuntimeError("boom")])
def test_understanding_failure_is_an_honest_unknown(chat, error):
    u = Understander(REGISTRY, FakeProvider(raises=error))
    assert chat("kuch bhi", understander=u).outcome.intent == "unknown"


def test_the_model_is_asked_through_the_neutral_interface(chat):
    r, provider = router_for([("help", {})])
    chat("kuch bhi", understander=r)
    req = provider.chooses[0]
    assert [t["name"] for t in req["tools"]] == REGISTRY.names()           # one source of truth
    assert all(set(t) == {"name", "description", "input_schema"} for t in req["tools"])   # no vendor shape
    assert "data, not instructions" in req["system"] and "%" not in req["system"]


def test_the_model_cannot_be_given_more_actions_than_the_cap(chat):
    provider = FakeProvider(turns=[[("help", {})] * 10])
    u = Understander(REGISTRY, provider, max_actions=2)
    assert len(u._call("x", "")) == 2


# ---------------------------------------------------------------- what to speak it in
@pytest.mark.parametrize("code,expected", [
    ("hi-IN", "hi-IN"), ("ta-IN", "ta-IN"), ("hi-Latn", "hi-IN"),   # Hinglish is spoken by the Hindi voice
    ("en-IN", "en-IN"), ("fr-FR", "en-IN"), ("", "en-IN"),           # no voice for it -> English
])
def test_speakable(code, expected):
    assert speakable(code) == expected


# ---------------------------------------------------------------- through the HTTP pipeline
@pytest.fixture
def api_client():
    return TestClient(create_app(":memory:"))


def test_pipeline_without_a_key_says_so_rather_than_guessing(api_client):
    r = api_client.post("/process", data={"user_id": "p1", "text": "कुछ भी"}).json()
    assert r["intent"] == "unknown" and "understanding_unavailable" in r["warnings"]
    assert r["normalized_text"] == "कुछ भी"


def test_pipeline_voice_in_voice_out(api_client, monkeypatch):
    """The mock speech-to-text hears Hinglish; with no model the reply is English, and it is still spoken."""
    r = api_client.post("/process", data={"user_id": "p2"},
                        files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert r["normalized_text"] == "aaj Swiggy se 1400 aaye aur petrol 250 ka dala"
    assert base64.b64decode(r["reply_audio_base64"])[:4] == b"OggS"


def test_messages_are_audited(api_client):
    api_client.post("/process", data={"user_id": "p5", "text": "कुछ भी"})
    row = api_client.app.state.db.execute("SELECT intent, actions_json FROM messages").fetchone()
    assert row["intent"] == "unknown"


# ---------------------------------------------------------------- digit grouping
@pytest.mark.parametrize("ours,theirs", [
    ("Today's profit is ₹1,400.", "Aaj ka munafa 1400 hai"),      # we grouped, the model did not
    ("Today's profit is ₹1400.", "Aaj ka munafa ₹1,400 hai"),     # the model grouped, we did not
    ("income ₹1,25,000", "aamdani ₹125000"),                      # Indian grouping
])
def test_grouping_differences_are_not_treated_as_invented_numbers(ours, theirs):
    """Regression: `1,400` and `1400` are the same figure. Comparing them naively rejected good
    renderings and left riders reading English."""
    rend, _ = renderer_for("hi-Latn", theirs)
    reply, _, warnings = rend.render(ours, "kitna hua")
    assert reply == theirs and warnings == []


def test_grouping_does_not_hide_a_real_invention():
    rend, _ = renderer_for("hi-Latn", "Aaj ka munafa 1,500 hai")
    assert rend.render("Today's profit is ₹1,400.", "kitna hua")[2][0].startswith("reply_numbers_invented")


def test_a_grouped_amount_in_the_transcript_is_grounded(chat):
    r, _ = router_for([("log_entries", {"entries": [{"type": "income", "amount": 1400, "platform": "Swiggy"}]})])
    assert "₹1,400" in chat("Swiggy se 1,400 aaye", understander=r).outcome.reply


# ---------------------------------------------------------------- regression: yes/no must not swallow the model's messages
def test_a_sentence_containing_nahi_reaches_the_model_and_does_not_discard_the_pending_entry(chat):
    """Regression. `is_no` used to match ANY short sentence containing a refusal word, so "kuch samajh nahi
    aaya" was answered locally - and with an entry pending it silently discarded the rider's entry."""
    r1, _ = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 250, "category": "fuel"}]})])
    chat("250 ka petrol", understander=r1)
    assert tools.get_pending(chat.conn, "u", NOW) is not None
    r2, api = router_for([("help", {})])
    out = chat("kuch samajh nahi aaya", understander=r2)
    assert len(api.chooses) == 1 and out.outcome.intent == "help"          # the model saw it
    assert tools.get_pending(chat.conn, "u", NOW) is not None              # and the entry survived


def test_cancel_my_last_entry_is_a_delete_request_not_a_refusal(chat):
    r1, _ = router_for([("log_entries", {"entries": [{"type": "income", "amount": 500}]})])
    chat("500 aaya", understander=r1)
    chat("haan")
    r2, api = router_for([("delete_entry", {})])
    out = chat("cancel my last entry", understander=r2)
    assert len(api.chooses) == 1 and "Delete the last entry" in out.outcome.reply


# ---------------------------------------------------------------- regression: "yes" while the type is still unknown
def test_yes_while_awaiting_the_type_asks_again_instead_of_crashing(chat):
    """Regression. When the rules were deleted, nothing stopped a bare "haan" from committing an entry whose
    type was still unknown, and the database rejected it (NOT NULL on transactions.type): a 500 in a demo."""
    r, api = router_for([("log_entries", {"entries": [{"amount": 500}]})])
    assert chat("500", understander=r).outcome.reply == "Was ₹500 income or an expense?"
    n = len(api.chooses)
    out = chat("haan", understander=r)
    assert out.outcome.reply == "Was ₹500 income or an expense?" and out.outcome.intent == "clarify"
    assert len(api.chooses) == n                                     # answered locally
    assert saved(chat) == [] and tools.get_pending(chat.conn, "u", NOW)["awaiting"] == "type"


def test_the_type_answer_then_goes_to_the_model_which_completes_the_entry(chat):
    r1, _ = router_for([("log_entries", {"entries": [{"amount": 500}]})])
    chat("500", understander=r1)
    r2, _ = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 500, "category": "fuel"}]})])
    assert "Please confirm: expense ₹500 on fuel" in chat("petrol tha", understander=r2).outcome.reply
    chat("haan")
    assert saved(chat)[0]["type"] == "expense"


def test_only_the_waiting_entry_s_amount_counts_as_already_said(chat):
    """The exemption is narrow: an amount from the entry still waiting is grounded; any other number the
    rider did not say is still refused."""
    r1, _ = router_for([("log_entries", {"entries": [{"amount": 500}]})])
    chat("500", understander=r1)
    r2, _ = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 900, "category": "fuel"}]})])
    out = chat("petrol tha", understander=r2)
    assert "₹900" not in out.outcome.reply and saved(chat) == []


def test_the_model_is_told_what_the_waiting_entry_contains(chat):
    r1, _ = router_for([("log_entries", {"entries": [{"amount": 500, "platform": "Swiggy"}]})])
    chat("500 swiggy", understander=r1)
    r2, api = router_for([("help", {})])
    chat("kharcha tha", understander=r2)
    sent = api.chooses[0]["user"]
    assert "waiting entry: 500 rupees" in sent and "platform Swiggy" in sent
    assert "just asked whether that was income or an expense" in sent


def test_with_no_model_the_reply_says_key_not_set_not_could_not_understand(chat):
    """The organisers start the app without keys. 'I could not understand' reads as the product failing;
    the honest reason is that no model key is set."""
    out = chat("aaj swiggy se 1400 aaye", understander=None, renderer=None)
    assert "key is not set" in out.outcome.reply and "could not understand" not in out.outcome.reply
    assert out.outcome.intent == "unknown" and "understanding_unavailable" in out.warnings


def test_with_a_model_that_fails_the_reply_is_still_could_not_understand(chat):
    out = chat("kuch bhi", understander=Understander(REGISTRY, FakeProvider(turns=[[]])), renderer=None)
    assert "could not understand" in out.outcome.reply and "key" not in out.outcome.reply
