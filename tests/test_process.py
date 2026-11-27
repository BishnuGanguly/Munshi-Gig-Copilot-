"""`POST /process` end to end, with a fake model standing in for the API.

This is the whole path: ffmpeg -> speech-to-text -> digits -> the model picks an action -> our code runs it
-> the model rewords the reply -> a voice note.
"""
import base64

import pytest
from fastapi.testclient import TestClient

from app.core import brain
from app.core.actions import REGISTRY
from app.main import create_app
from tests.audio_helpers import make_audio, silent_audio
from tests.fakes import FakeProvider, rendering

KEYS = {"transcript", "detected_language", "normalized_text", "entries", "reply_english",
        "reply_user_language", "warnings", "intent", "reply_audio_mime", "reply_audio_base64"}


@pytest.fixture
def client():
    return TestClient(create_app(":memory:"))


@pytest.fixture
def with_model(monkeypatch):
    """Install a fake provider, as if BRAIN=... were configured. `turns` is one tool-call list per request."""
    def install(turns, rendered=None, language="hi-IN"):
        provider = FakeProvider(turns=turns, text=rendering(language, rendered) if rendered is not None else "")
        monkeypatch.setattr(brain, "get_understander", lambda: brain.Understander(REGISTRY, provider))
        if rendered is not None:
            monkeypatch.setattr(brain, "get_renderer", lambda: brain.Renderer(provider))
        return provider
    return install


# ---------------------------------------------------------------- shape and inputs
def test_response_shape(client):
    r = client.post("/process", data={"user_id": "u1", "text": "Swiggy se 1400 aaye"})
    assert r.status_code == 200 and set(r.json()) == KEYS


def test_text_is_passed_through_with_amounts_as_digits(client):
    r = client.post("/process", data={"user_id": "u1", "text": "zomato se dedh hazaar mile"}).json()
    assert r["transcript"] == "zomato se dedh hazaar mile"
    assert r["normalized_text"] == "Zomato se 1500 mile"      # what the model is given


def test_audio_is_transcribed(client):
    r = client.post("/process", data={"user_id": "u1"},
                    files={"audio": ("note.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert r["transcript"] == "aaj swiggy se 1400 aaye aur petrol 250 ka dala"
    assert r["detected_language"] == "hi-IN"                   # reported by speech-to-text


@pytest.mark.parametrize("data,files,detail", [
    ({"user_id": "u1"}, None, "either an audio file or non-empty text"),
    ({"user_id": "u1"}, {"audio": ("n.ogg", b"", "audio/ogg")}, "Empty audio"),
    ({"user_id": "u1", "text": "hi", "voice": "loud"}, None, "voice must be one of"),
])
def test_bad_input_is_400(client, data, files, detail):
    r = client.post("/process", data=data, files=files)
    assert r.status_code == 400 and detail in r.json()["detail"]


def test_silent_audio_is_rejected_before_any_model_call(client):
    r = client.post("/process", data={"user_id": "u1"}, files={"audio": ("n.wav", silent_audio(), "audio/wav")})
    assert r.status_code == 400 and ("silent" in r.json()["detail"].lower()
                                     or "speech" in r.json()["detail"].lower())


def test_without_a_model_it_says_so_rather_than_guessing(client):
    r = client.post("/process", data={"user_id": "u1", "text": "Got 1400 from Swiggy"}).json()
    assert r["intent"] == "unknown" and "understanding_unavailable" in r["warnings"]
    assert r["entries"] == []


# ---------------------------------------------------------------- the full path, with a fake model
def test_voice_note_to_confirmed_entry(client, with_model):
    """Speech-to-text hears Hinglish; the model logs two entries; 'haan' saves them without a model call."""
    api = with_model([
        # request 1: the voice note -> two entries. request 2 is "haan" (never reaches the model).
        [("log_entries", {"entries": [{"type": "income", "amount": 1400, "platform": "Swiggy"},
                                      {"type": "expense", "amount": 250, "category": "fuel"}]})],
        # request 2 (the model's second call): the question
        [("query_summary", {"kind": "earnings", "period": "today"})],
    ])

    r = client.post("/process", data={"user_id": "e2e"},
                    files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert [(e["type"], e["amount"], e["platform"]) for e in r["entries"]] == [
        ("income", 1400, "Swiggy"), ("expense", 250, None)]
    assert all(e["status"] == "pending_confirmation" for e in r["entries"])
    assert "Please confirm" in r["reply_english"]
    assert base64.b64decode(r["reply_audio_base64"])[:4] == b"OggS"
    assert "aaj Swiggy se 1400" in api.chooses[0]["user"]   # the rider's own words

    n = len(api.chooses)
    r2 = client.post("/process", data={"user_id": "e2e", "text": "haan"}).json()
    assert r2["reply_english"] == "Saved. Today's profit is ₹1,150."
    assert len(api.chooses) == n                                             # confirmations stay local

    r3 = client.post("/process", data={"user_id": "e2e", "text": "aaj kitna kamaya"}).json()
    assert r3["reply_english"].startswith("Today you earned ₹1,400")


def test_a_question_is_answered_from_the_database(client, with_model):
    with_model([[("query_summary", {"kind": "earnings", "period": "this_week"})]])
    r = client.post("/process", data={"user_id": "q1", "text": "इस हफ़्ते कितना कमाया"}).json()
    assert r["reply_english"] == "No income recorded this week." and r["intent"] == "query"


def test_the_renderer_cannot_invent_a_figure(client, with_model):
    with_model([[("query_summary", {"kind": "earnings", "period": "today"})]],
               rendered="आज आपने ₹9,999 कमाए")
    r = client.post("/process", data={"user_id": "q2", "text": "aaj kitna kamaya"}).json()
    assert r["reply_user_language"] == "No income recorded today."            # English kept
    assert any(w.startswith("reply_numbers_invented") for w in r["warnings"])


# ---------------------------------------------------------------- voice modes
def test_typed_text_gets_text_only_by_default(client):
    r = client.post("/process", data={"user_id": "v1", "text": "hello"}).json()
    assert r["reply_audio_base64"] is None and r["reply_audio_mime"] is None


def test_voice_always_and_never(client):
    r = client.post("/process", data={"user_id": "v2", "text": "hello", "voice": "always"}).json()
    assert r["reply_audio_base64"] is not None
    r = client.post("/process", data={"user_id": "v2", "voice": "never"},
                    files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert r["reply_audio_base64"] is None


# ---------------------------------------------------------------- audit trail
def test_messages_are_recorded(client, with_model):
    with_model([[("query_summary", {"kind": "earnings", "period": "today"})]])
    client.post("/process", data={"user_id": "a1", "text": "aaj kitna kamaya"})
    row = client.app.state.db.execute(
        "SELECT intent, actions_json, latency_ms FROM messages WHERE user_id = 'a1'").fetchone()
    assert row["intent"] == "query" and "query_summary" in row["actions_json"]
    assert row["latency_ms"] >= 0


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
