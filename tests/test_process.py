from fastapi.testclient import TestClient

from app.main import create_app

KEYS = {"transcript", "detected_language", "english_text", "entries", "reply_english", "reply_user_language"}


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
    r = client().post("/process", data={"user_id": "u1"}, files={"audio": ("note.ogg", b"fake-audio", "audio/ogg")})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == KEYS
    assert body["detected_language"] == "hi-IN"


def test_requires_audio_or_text():
    assert client().post("/process", data={"user_id": "u1"}).status_code == 400


def test_empty_audio_rejected():
    r = client().post("/process", data={"user_id": "u1"}, files={"audio": ("n.ogg", b"", "audio/ogg")})
    assert r.status_code == 400


def test_language_remembered_for_text():
    c = client()
    c.post("/process", data={"user_id": "u2"}, files={"audio": ("n.ogg", b"x", "audio/ogg")})
    r = c.post("/process", data={"user_id": "u2", "text": "kitna kamaya"})
    assert r.json()["detected_language"] == "hi-IN"
