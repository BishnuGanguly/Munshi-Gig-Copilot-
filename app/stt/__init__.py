from app.config import get_settings
from .base import SttProvider, Transcript
from .mock import MockStt


def get_stt() -> SttProvider:
    provider = get_settings().stt_provider
    if provider == "mock":
        return MockStt()
    raise ValueError(f"Unknown STT_PROVIDER: {provider!r} (only 'mock' exists in Step 1)")


__all__ = ["get_stt", "SttProvider", "Transcript"]
