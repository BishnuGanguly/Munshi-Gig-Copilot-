"""Keep tests hermetic: never use the developer's real .env settings or API key, never call real services."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "mock")
    monkeypatch.setenv("STT_FALLBACK", "none")
    monkeypatch.setenv("TTS_PROVIDER", "mock")
    monkeypatch.setenv("BRAIN", "none")
    monkeypatch.delenv("BRAIN_MODEL", raising=False)
    monkeypatch.delenv("BRAIN_RENDER_MODEL", raising=False)
    monkeypatch.delenv("SARVAM_TTS_MODEL", raising=False)
    monkeypatch.delenv("SARVAM_TTS_SPEAKER", raising=False)
    monkeypatch.delenv("VOICE_MAX_CHARS", raising=False)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.delenv("SARVAM_LANGUAGE_CODE", raising=False)
    monkeypatch.delenv("SARVAM_MODEL", raising=False)
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
