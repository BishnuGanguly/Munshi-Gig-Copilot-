from app.config import get_settings
from .base import TranslateProvider
from .mock import MockTranslate


def get_translator() -> TranslateProvider:
    provider = get_settings().translate_provider
    if provider == "mock":
        return MockTranslate()
    raise ValueError(f"Unknown TRANSLATE_PROVIDER: {provider!r} (only 'mock' exists in Step 1)")


__all__ = ["get_translator", "TranslateProvider"]
