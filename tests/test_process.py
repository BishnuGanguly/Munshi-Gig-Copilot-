from fastapi.testclient import TestClient

from app.main import create_app
from tests.audio_helpers import make_audio, silent_audio

KEYS = {"transcript", "detected_language", "normalized_text", "english_text", "entries", "reply_english",
        "reply_user_language", "warnings", "intent", "reply_audio_mime", "reply_audio_base64"}


def client():
    return TestClient(create_app(":memory:"))


def test_health():
    assert client().get("/health").json() == {"status": "ok"}


def test_process_text():
    r = client().post("/process", data={"user_id": "u1", "text": "Swiggy se 1400 aaye"})
    assert r.status_code == 200
    assert set(r.json()) == KEYS
    assert r.json()["transcript"] == "Swiggy se 1400 aaye"


def test_process_audio_uses_mock_stt():
    r = client().post("/process", data={"user_id": "u1"}, files={"audio": ("note.ogg", make_audio("ogg"), "audio/ogg")})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == KEYS
    assert body["detected_language"] == "hi-IN"


def test_requires_audio_or_text():
    assert client().post("/process", data={"user_id": "u1"}).status_code == 400


def test_empty_audio_rejected():
    r = client().post("/process", data={"user_id": "u1"}, files={"audio": ("n.ogg", b"", "audio/ogg")})
    assert r.status_code == 400


def test_language_remembered_for_digit_only_text():
    c = client()
    c.post("/process", data={"user_id": "u2"}, files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")})
    r = c.post("/process", data={"user_id": "u2", "text": "1400"})
    assert r.json()["detected_language"] == "hi-IN"  # no script clue, so the user's last language is used


def test_romanised_hindi_text_is_detected_as_hinglish():
    r = client().post("/process", data={"user_id": "u3", "text": "aaj swiggy se 1400 aaye"})
    assert r.json()["detected_language"] == "hi-Latn"
    assert "1400" in r.json()["normalized_text"]


def test_spoken_amount_words_become_digits_in_the_pipeline():
    r = client().post("/process", data={"user_id": "u4", "text": "आज मैं दो सौ पचास रुपए का बचत किया।"})
    assert r.json()["detected_language"] == "hi-IN"
    assert "250" in r.json()["normalized_text"]


def test_silent_audio_gets_friendly_400():
    r = client().post("/process", data={"user_id": "u1"}, files={"audio": ("n.wav", silent_audio(), "audio/wav")})
    assert r.status_code == 400 and "silent" in r.json()["detail"].lower() or "speech" in r.json()["detail"].lower()


def test_voice_note_to_confirmed_entry_end_to_end():
    """Mock STT hears 'aaj swiggy se 1400 aaye aur petrol 250 ka dala'; the core parses and saves on 'yes'."""
    c = client()
    r = c.post("/process", data={"user_id": "e2e"}, files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert [(e["type"], e["amount"], e["platform"], e["category"]) for e in r["entries"]] == [
        ("income", 1400, "Swiggy", "earnings"), ("expense", 250, None, "fuel")]
    assert all(e["status"] == "pending_confirmation" for e in r["entries"]) and "Please confirm" in r["reply_english"]
    r2 = c.post("/process", data={"user_id": "e2e", "text": "yes"}).json()
    assert r2["reply_english"] == "Saved. Today's profit is ₹1,150."
    r3 = c.post("/process", data={"user_id": "e2e", "text": "how much did I earn today"}).json()
    assert r3["reply_english"].startswith("Today you earned ₹1,400")
