"""The LLM-first pipeline (`PIPELINE=llm`). Fake clients stand in for the API, so these tests pin down what
the code does with whatever the model returns - including output that invents numbers."""
import base64
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core import brain, dates, db, tools
from app.core.actions import REGISTRY
from app.core.brain import Renderer, preprocess, speakable
from app.core.llm_router import LlmRouter
from app.main import create_app
from tests.audio_helpers import make_audio

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)


# ---------------------------------------------------------------- fakes
class Block:
    def __init__(self, name, args):
        self.type, self.name, self.input = "tool_use", name, args


class FakeApi:
    """Records requests; returns queued tool calls (router) or text (renderer)."""

    def __init__(self, calls=(), text=None, stop_reason="tool_use", raises=None):
        self.calls, self.text, self.stop_reason, self.raises = list(calls), text, stop_reason, raises
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        if self.raises:
            raise self.raises
        if self.text is not None:
            content = [type("T", (), {"type": "text", "text": self.text})()]
        else:
            content = [Block(n, a) for n, a in self.calls]
        return type("R", (), {"content": content, "stop_reason": self.stop_reason, "stop_details": None,
                              "usage": type("U", (), {"cache_read_input_tokens": 0})()})()


def client_of(api):
    return type("C", (), {"messages": api})()


def router_for(calls):
    api = FakeApi(calls=calls)
    return LlmRouter(REGISTRY, client=client_of(api), system=brain.SYSTEM_UNDERSTAND), api


def renderer_for(language="hi-IN", reply="theek hai", **kw):
    api = FakeApi(text=json.dumps({"language": language, "reply": reply}), **kw)
    return Renderer(client=client_of(api)), api


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
    ("zomato se dedh hazaar mile", "zomato se 1500 mile"),
    ("saadhe teen sau kharcha", "350 kharcha"),
    ("१४०० आए", "1400 आए"),
    ("इन்று 1400 கிடைத்தது", "इन்று 1400 கிடைத்தது"),
])
def test_amounts_become_digits_before_the_model_sees_them(raw, expected):
    """Arithmetic stays in code: deterministic, free, and it is what lets the number guard work."""
    assert preprocess(raw) == expected


def test_confirmations_never_reach_the_model(chat):
    r, api = router_for([("log_entries", {"entries": [{"type": "income", "amount": 500}]})])
    chat("500 aaya", router=r)
    calls_before = len(api.requests)
    out = chat("haan theek hai", router=r)                     # yes, in Hinglish
    assert out.outcome.reply.startswith("Saved")
    assert len(api.requests) == calls_before                   # no round-trip for the commonest message
    assert saved(chat)[0]["amount"] == 500


def test_no_discards_locally(chat):
    r, api = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 250, "category": "fuel"}]})])
    chat("250 petrol", router=r)
    n = len(api.requests)
    assert "discarded" in chat("nahi", router=r).outcome.reply
    assert len(api.requests) == n and saved(chat) == []


def test_yes_with_nothing_pending(chat):
    r, api = router_for([])
    assert chat("yes", router=r).outcome.reply.startswith("There is nothing")
    assert api.requests == []


# ---------------------------------------------------------------- the model understands, our code executes
def test_regional_language_logging_end_to_end(chat):
    r, _ = router_for([("log_entries", {"entries": [{"type": "income", "amount": 1400, "platform": "Swiggy"}]})])
    rend, rapi = renderer_for("ta-IN", "இன்று ₹1,400 வரவு Swiggy. சரியா?")
    out = chat("இன்று ஸ்விக்கியில் 1400 ரூபாய் கிடைத்தது", router=r, renderer=rend)
    assert "Please confirm: income ₹1,400 from Swiggy" in out.outcome.reply      # our English fact
    assert out.reply_user == "இன்று ₹1,400 வரவு Swiggy. சரியா?"                  # rendered for the rider
    assert out.reply_language == "ta-IN" and saved(chat) == []                   # still unconfirmed
    assert "இன்று ஸ்விக்கியில் 1400" in rapi.requests[0]["messages"][0]["content"]


def test_no_translation_call_is_made(chat):
    """The rider's words go to the model as they are - there is no to_english hop any more."""
    r, api = router_for([("query_summary", {"kind": "earnings", "period": "this_week"})])
    chat("इस हफ़्ते कितना कमाया", router=r, renderer=None)
    sent = api.requests[0]["messages"][0]["content"]
    assert "इस हफ़्ते कितना कमाया" in sent


def test_a_correction_while_pending_replaces_it(chat):
    r1, _ = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 250, "category": "fuel"}]})])
    chat("250 ka petrol", router=r1)
    r2, api2 = router_for([("log_entries", {"entries": [{"type": "expense", "amount": 300, "category": "fuel"}]})])
    out = chat("250 nahi 300 tha", router=r2)
    assert "₹300" in out.outcome.reply
    assert "An action is already waiting" in api2.requests[0]["messages"][0]["content"]   # the model is told
    chat("haan")
    assert saved(chat)[0]["amount"] == 300


def test_target_flow(chat):
    r, _ = router_for([("set_target", {"amount": 1500})])
    assert "₹1,500" in chat("roz 1500 kamana hai", router=r).outcome.reply
    chat("haan")
    assert tools.get_target(chat.conn, "u", NOW.date()) == 1500


def test_invented_amounts_are_still_dropped(chat):
    r, _ = router_for([("log_entries", {"entries": [{"type": "income", "amount": 9999}]})])
    out = chat("Swiggy ka paisa aaya", router=r)
    assert "9,999" not in out.outcome.reply and saved(chat) == []


def test_without_a_model_it_says_so_rather_than_guessing(chat):
    out = chat("कुछ भी", router=None, renderer=None)
    assert out.outcome.intent == "unknown" and "understanding_unavailable" in out.warnings


# ---------------------------------------------------------------- the renderer, and the number guard
def test_render_request_shape():
    rend, api = renderer_for()
    rend.render("Saved. Today's profit is ₹350.", "250 ka petrol")
    req = api.requests[0]
    assert req["model"] == "claude-haiku-5-5" and req["output_config"] == {"effort": "low"}
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in req                                   # rendering is not a tool call
    assert "SAME \nlanguage" in req["system"][0]["text"] or "SAME" in req["system"][0]["text"]


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


@pytest.mark.parametrize("text,kw", [
    (None, {"raises": RuntimeError("connection reset")}),
    ("not json", {}),
    ('{"language": "hi-IN"}', {}),
    ('{"language": "hi-IN", "reply": ""}', {}),
])
def test_render_failures_keep_the_english_reply(text, kw):
    api = FakeApi(text=text if text is not None else "x", **kw)
    reply, lang, warnings = Renderer(client=client_of(api)).render("Saved.", "x")
    assert reply == "Saved." and lang == "en-IN" and warnings == ["reply_not_rendered"]


def test_render_refusal_keeps_the_english_reply():
    api = FakeApi(text='{"language":"hi-IN","reply":"x"}', stop_reason="refusal")
    assert Renderer(client=client_of(api)).render("Saved.", "x")[0] == "Saved."


# ---------------------------------------------------------------- what to speak it in
@pytest.mark.parametrize("code,expected", [
    ("hi-IN", "hi-IN"), ("ta-IN", "ta-IN"), ("hi-Latn", "hi-IN"),   # Hinglish is spoken by the Hindi voice
    ("en-IN", "en-IN"), ("fr-FR", "en-IN"), ("", "en-IN"),           # no voice for it -> English
])
def test_speakable(code, expected):
    assert speakable(code) == expected


# ---------------------------------------------------------------- through the HTTP pipeline
@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("PIPELINE", "llm")
    return TestClient(create_app(":memory:"))


def test_pipeline_llm_mode_without_a_key_still_answers(api_client):
    r = api_client.post("/process", data={"user_id": "p1", "text": "कुछ भी"}).json()
    assert r["intent"] == "unknown" and "understanding_unavailable" in r["warnings"]
    assert r["normalized_text"] == "कुछ भी"


def test_pipeline_llm_mode_voice_in_voice_out(api_client, monkeypatch):
    """The mock speech-to-text hears Hinglish; with no model the reply is English, and it is still spoken."""
    r = api_client.post("/process", data={"user_id": "p2"},
                        files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert r["normalized_text"] == "aaj swiggy se 1400 aaye aur petrol 250 ka dala"
    assert base64.b64decode(r["reply_audio_base64"])[:4] == b"OggS"


def test_pipeline_rules_mode_is_still_the_default():
    r = TestClient(create_app(":memory:")).post(
        "/process", data={"user_id": "p3", "text": "Got 1400 from Swiggy"}).json()
    assert "Please confirm: income ₹1,400 from Swiggy" in r["reply_english"]


def test_bad_pipeline_name_is_rejected(monkeypatch):
    monkeypatch.setenv("PIPELINE", "magic")
    r = TestClient(create_app(":memory:")).post("/process", data={"user_id": "p4", "text": "hi"})
    assert r.status_code == 400 and "PIPELINE must be one of" in r.json()["detail"]


def test_messages_are_audited_in_llm_mode(api_client):
    api_client.post("/process", data={"user_id": "p5", "text": "कुछ भी"})
    row = api_client.app.state.db.execute("SELECT intent, actions_json FROM messages").fetchone()
    assert row["intent"] == "unknown"
