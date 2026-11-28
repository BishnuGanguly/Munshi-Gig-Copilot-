"""What is configured, for the key banner on the web page. Reports booleans and provider names only:
never a key, a URL with credentials, or any part of a secret."""
import importlib.util
import os

from app.llm import get_provider
from app.llm.openai_compat import OpenAICompatProvider


def _provider_ready(p) -> bool:
    if isinstance(p, OpenAICompatProvider):
        return p.auth == "none" or bool(p.api_key)
    return False


def model_status() -> dict:
    provider = get_provider()
    if provider is None:
        return {"provider": "none", "ready": False,
                "message": "Model not configured (BRAIN=none): messages cannot be understood."}
    if _provider_ready(provider):
        return {"provider": provider.name, "ready": True, "message": f"Model: {provider.name}."}
    return {"provider": provider.name, "ready": False,
            "message": f"Model key not set for {provider.name}: messages cannot be understood."}


def _service(kind: str, env: str) -> dict:
    name = os.getenv(env, "mock")
    if name == "sarvam":
        if os.getenv("SARVAM_API_KEY"):
            return {"provider": "sarvam", "ready": True, "message": f"{kind}: Sarvam."}
        return {"provider": "sarvam", "ready": False, "message": f"{kind}: Sarvam key not set."}
    if name == "faster_whisper":
        ok = importlib.util.find_spec("faster_whisper") is not None
        return {"provider": name, "ready": ok,
                "message": f"{kind}: offline faster-whisper." if ok else f"{kind}: faster-whisper is not installed."}
    return {"provider": name, "ready": False, "message": {
        "Speech-to-text": "Speech-to-text key not set: voice notes go to a mock that always hears one fixed sentence.",
        "Voice replies": "Voice key not set: spoken replies are a test beep."}[kind]}


def status() -> dict:
    return {"model": model_status(),
            "speech_to_text": _service("Speech-to-text", "STT_PROVIDER"),
            "voice": _service("Voice replies", "TTS_PROVIDER")}
