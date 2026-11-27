import io
import wave

import httpx
import pytest

from app.errors import SttUnavailableError, UnclearAudioError
from app.stt import FallbackStt
from app.stt.base import Transcript
from app.stt.sarvam import SarvamStt, split_wav
from tests.audio_helpers import make_audio


def sarvam(handler, **kw):
    return SarvamStt(api_key="k", transport=httpx.MockTransport(handler), retries=kw.pop("retries", 1), **kw)


def test_sarvam_parses_response_and_sends_auth():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["api-subscription-key"]
        seen["body"] = request.content
        return httpx.Response(200, json={"transcript": "aaj swiggy se 1400 aaye", "language_code": "hi-IN"})

    tr = sarvam(handler).transcribe(make_audio("wav", 2))
    assert (tr.text, tr.language) == ("aaj swiggy se 1400 aaye", "hi-IN")
    assert seen["key"] == "k" and b"language_code" in seen["body"] and b"model" in seen["body"]


def test_sarvam_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json={"transcript": "hi", "language_code": "en-IN"})

    assert sarvam(handler).transcribe(make_audio("wav", 1)).text == "hi"
    assert calls["n"] == 2


def test_sarvam_bad_key_no_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    with pytest.raises(SttUnavailableError, match="rejected the API key"):
        sarvam(handler, retries=3).transcribe(make_audio("wav", 1))
    assert calls["n"] == 1


def test_sarvam_timeout_becomes_unavailable(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    def handler(request):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(SttUnavailableError):
        sarvam(handler).transcribe(make_audio("wav", 1))


def test_missing_key():
    with pytest.raises(SttUnavailableError, match="SARVAM_API_KEY"):
        SarvamStt(transport=httpx.MockTransport(lambda r: httpx.Response(200))).transcribe(make_audio("wav", 1))


def test_non_json_response_is_unavailable_not_a_crash():
    with pytest.raises(SttUnavailableError, match="non-JSON"):
        sarvam(lambda r: httpx.Response(200, text="oops")).transcribe(make_audio("wav", 1))


def test_long_audio_is_chunked_and_joined():
    wav = make_audio("wav", 70)
    assert len(split_wav(wav, 28)) == 3
    n = {"i": 0}

    def handler(request):
        n["i"] += 1
        return httpx.Response(200, json={"transcript": f"part{n['i']}", "language_code": "hi-IN"})

    assert sarvam(handler).transcribe(wav).text == "part1 part2 part3"


class Fake:
    def __init__(self, name, result):
        self.name, self.result = name, result

    def transcribe(self, wav):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_fallback_used_when_primary_down():
    chain = FallbackStt([Fake("a", SttUnavailableError("down")), Fake("b", Transcript("ok", "hi-IN", 0.9))], 0.3)
    assert chain.transcribe(b"").text == "ok"


def test_low_confidence_falls_through_then_unclear():
    chain = FallbackStt([Fake("a", Transcript("mumble", "hi-IN", 0.1)), Fake("b", Transcript("", "hi-IN", 0.9))], 0.3)
    with pytest.raises(UnclearAudioError):
        chain.transcribe(b"")


def test_all_providers_down():
    chain = FallbackStt([Fake("a", SttUnavailableError("x"))], 0.3)
    with pytest.raises(SttUnavailableError):
        chain.transcribe(b"")


def test_whisper_missing_dependency_message():
    from app.stt.whisper_local import WhisperStt
    with pytest.raises(SttUnavailableError, match="faster-whisper"):
        WhisperStt().transcribe(make_audio("wav", 1))
