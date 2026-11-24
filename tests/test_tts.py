import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.audio import to_voice_note
from app.errors import TtsUnavailableError
from app.main import create_app
from app.translate import get_language_service
from app.translate.languages import HINGLISH
from app.tts.mock import MockTts
from app.tts.sarvam import SarvamTts
from app.tts.service import make_voice_note
from tests.audio_helpers import make_audio


def b64wav(seconds=1.0):
    return base64.b64encode(make_audio("wav", seconds)).decode()


def sarvam(handler, **kw):
    return SarvamTts(api_key="k", transport=httpx.MockTransport(handler), retries=kw.pop("retries", 1), **kw)


# ---------------------------------------------------------------- conversion and mock
def test_voice_note_is_ogg_opus():
    ogg = to_voice_note(MockTts().synthesize("Saved. Today's profit is ₹1,150.", "en-IN"))
    assert ogg[:4] == b"OggS" and b"OpusHead" in ogg[:200]


# ---------------------------------------------------------------- Sarvam TTS (mocked HTTP)
def test_sarvam_tts_request_and_decoding():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["api-subscription-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"audios": [b64wav()]})

    wav = sarvam(handler).synthesize("आज का मुनाफ़ा 1150 है", "hi-IN")
    assert wav[:4] == b"RIFF" and seen["key"] == "k"
    assert seen["body"] == {"text": "आज का मुनाफ़ा 1150 है", "target_language_code": "hi-IN", "speaker": "anushka", "model": "bulbul:v2"}


def test_sarvam_tts_retries_with_other_language_field_name():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(422, text="bad field") if "target_language_code" in bodies[-1] else httpx.Response(200, json={"audios": [b64wav()]})

    assert sarvam(handler).synthesize("hello", "en-IN")[:4] == b"RIFF"
    assert "language_code" in bodies[1] and "target_language_code" not in bodies[1]


def test_sarvam_tts_long_text_is_chunked_and_joined():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"audios": [b64wav(1.0)]})

    wav = sarvam(handler).synthesize(("यह एक वाक्य है। " * 300).strip(), "hi-IN")
    assert len(calls) > 1 and all(len(c) <= 1400 for c in calls)
    import io, wave
    with wave.open(io.BytesIO(wav)) as f:
        assert f.getnframes() / f.getframerate() > 1.5   # several chunks joined, not just the first


@pytest.mark.parametrize("status,match", [(403, "API key"), (500, "failed after")])
def test_sarvam_tts_errors(status, match, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(TtsUnavailableError, match=match):
        sarvam(lambda r: httpx.Response(status, text="x")).synthesize("hi", "en-IN")


def test_sarvam_tts_bad_payloads(monkeypatch):
    with pytest.raises(TtsUnavailableError, match="SARVAM_API_KEY"):
        SarvamTts(transport=httpx.MockTransport(lambda r: httpx.Response(200))).synthesize("hi", "en-IN")
    with pytest.raises(TtsUnavailableError):
        sarvam(lambda r: httpx.Response(200, json={"nope": 1})).synthesize("hi", "en-IN")
    with pytest.raises(TtsUnavailableError, match="decoded"):
        sarvam(lambda r: httpx.Response(200, json={"audios": [base64.b64encode(b"not a wav").decode()]})).synthesize("hi", "en-IN")


# ---------------------------------------------------------------- choosing what to speak
class Recorder:
    name = "rec"

    def __init__(self):
        self.calls = []

    def synthesize(self, text, lang):
        self.calls.append((text, lang))
        return MockTts().synthesize(text, lang)


def speak(reply_user, reply_en, language, translated=True, tts=None):
    svc = get_language_service()           # mock translator (returns text unchanged)
    return make_voice_note(reply_user, reply_en, language, translated, svc, tts)


def test_speaks_the_users_language():
    rec = Recorder()
    ogg, w = speak("आज का मुनाफ़ा ₹1150", "Today's profit is ₹1150", "hi-IN", tts=rec)
    assert ogg[:4] == b"OggS" and rec.calls == [("आज का मुनाफ़ा ₹1150", "hi-IN")] and w == []


def test_english_and_untranslated_replies_are_spoken_in_english():
    rec = Recorder()
    speak("Saved.", "Saved.", "en-IN", tts=rec)
    speak("Saved.", "Saved.", "ta-IN", translated=False, tts=rec)
    assert [c[1] for c in rec.calls] == ["en-IN", "en-IN"]


def test_hinglish_text_is_spoken_in_hindi_voice():
    rec = Recorder()
    speak("Aaj ka profit 1150 hai", "Today's profit is 1150", HINGLISH, tts=rec)
    assert rec.calls[0][1] == "hi-IN"


def test_long_reply_stays_text_only():
    ogg, w = speak("x" * 500, "x" * 500, "en-IN")
    assert ogg is None and w == ["voice_skipped_long_reply"]


def test_tts_failure_never_raises():
    class Boom:
        name = "boom"

        def synthesize(self, text, lang):
            raise TtsUnavailableError("down")

    ogg, w = speak("Saved.", "Saved.", "en-IN", tts=Boom())
    assert ogg is None and w[0].startswith("voice_reply_failed")


# ---------------------------------------------------------------- pipeline and API
def client():
    return TestClient(create_app(":memory:"))


def test_voice_note_in_gets_voice_note_out():
    r = client().post("/process", data={"user_id": "v1"}, files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert base64.b64decode(r["reply_audio_base64"])[:4] == b"OggS" and r["reply_audio_mime"].startswith("audio/ogg")


def test_typed_text_gets_text_only_by_default():
    r = client().post("/process", data={"user_id": "v2", "text": "Got 1400 from Swiggy"}).json()
    assert r["reply_audio_base64"] is None and r["reply_audio_mime"] is None


def _voice_note():
    return {"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}


def test_voice_mode_always_voices_typed_text():
    r = client().post("/process", data={"user_id": "v3", "text": "hello", "voice": "always"}).json()
    assert r["reply_audio_base64"] is not None


def test_voice_mode_never_silences_a_voice_note():
    r = client().post("/process", data={"user_id": "v3", "voice": "never"}, files=_voice_note()).json()
    assert r["reply_audio_base64"] is None


def test_bad_voice_mode_is_400():
    assert client().post("/process", data={"user_id": "v4", "text": "hi", "voice": "loud"}).status_code == 400


def test_speak_endpoint():
    r = client().post("/speak", data={"text": "Saved.", "language": "hi-IN"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content[:4] == b"OggS"
    assert client().post("/speak", data={"text": "x", "language": "xx-YY"}).status_code == 400


def test_voice_failure_keeps_the_text_reply(monkeypatch):
    class Boom:
        name = "boom"

        def synthesize(self, text, lang):
            raise TtsUnavailableError("down")

    monkeypatch.setattr("app.tts.service.get_tts", lambda: Boom())
    r = client().post("/process", data={"user_id": "v5"}, files={"audio": ("n.ogg", make_audio("ogg"), "audio/ogg")}).json()
    assert r["reply_audio_base64"] is None and "Please confirm" in r["reply_english"]
    assert any(w.startswith("voice_reply_failed") for w in r["warnings"])
