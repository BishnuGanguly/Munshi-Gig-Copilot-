"""Provider selection.

TRANSLATE_PROVIDER=sarvam|llm|mock       primary
TRANSLATE_FALLBACK=llm|sarvam|none       tried when the primary fails or drops numbers (default none)
"""
import os

from .base import TranslateProvider
from .mock import MockTranslate
from .service import LanguageService


def _build(name: str) -> TranslateProvider:
    if name == "mock":
        return MockTranslate()
    if name == "sarvam":
        from .sarvam import SarvamTranslate
        return SarvamTranslate()
    if name == "llm":
        from .llm import LlmTranslate
        return LlmTranslate()
    raise ValueError(f"Unknown translate provider: {name!r} (use sarvam, llm or mock)")


def get_language_service() -> LanguageService:
    primary = os.getenv("TRANSLATE_PROVIDER", "mock")
    fallback = os.getenv("TRANSLATE_FALLBACK", "none")
    providers = [_build(primary)]
    if fallback and fallback != "none" and fallback != primary:
        providers.append(_build(fallback))
    return LanguageService(providers)


__all__ = ["get_language_service", "LanguageService", "TranslateProvider"]
