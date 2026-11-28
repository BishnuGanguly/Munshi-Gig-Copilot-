"""The model provider, chosen by configuration.

    BRAIN=sarvam                 Sarvam's chat model (sarvam-105b), billed in rupees, using SARVAM_API_KEY
    BRAIN=none                   off: the assistant says the model key is not set

Overrides: BRAIN_MODEL (or BRAIN_MODEL_SARVAM), BRAIN_BASE_URL_SARVAM, BRAIN_REASONING_SARVAM. Another provider
would be one new class implementing app/llm/base.py.
"""
import os

from .base import LlmProvider, ToolCall
from .openai_compat import OpenAICompatProvider

# Confirmed live: POST /v1/chat/completions, the key in an `api-subscription-key` header, model `sarvam-105b`.
SARVAM_URL = "https://api.sarvam.ai/v1"
SARVAM_MODEL = "sarvam-105b"
# sarvam-105b reasons by default and its reasoning tokens count against max_tokens. Choosing an action gets a
# little thinking; rewording a sentence gets none (null turns thinking off, per Sarvam's docs).
SARVAM_REASONING = {"choose": "low", "complete": "off"}
KNOWN = ["sarvam"]


def build_provider(name: str = "sarvam") -> LlmProvider:
    """The provider by name. Cheap: nothing is contacted and no key is needed until it is used."""
    name = name.strip().lower()
    if name != "sarvam":
        raise ValueError(f"Unknown BRAIN provider {name!r}. Choose from: none, {', '.join(KNOWN)}")
    override = os.getenv("BRAIN_REASONING_SARVAM")          # low | medium | high | off | omit, both calls
    reasoning = {"choose": override, "complete": override} if override else dict(SARVAM_REASONING)
    model = os.getenv("BRAIN_MODEL_SARVAM") or os.getenv("BRAIN_MODEL") or SARVAM_MODEL
    return OpenAICompatProvider("sarvam", os.getenv("BRAIN_BASE_URL_SARVAM") or SARVAM_URL, model,
                                os.getenv("SARVAM_API_KEY"), "header:api-subscription-key", reasoning=reasoning)


def _config_key() -> tuple:
    names = ("BRAIN", "BRAIN_MODEL", "BRAIN_MODEL_SARVAM", "BRAIN_BASE_URL_SARVAM", "BRAIN_REASONING_SARVAM",
             "SARVAM_API_KEY")
    return tuple(os.getenv(n) for n in names)


_cache: dict[tuple, LlmProvider | None] = {}


def get_provider() -> LlmProvider | None:
    """The configured provider, or None when BRAIN=none. Cached while the configuration is unchanged, so
    connections are reused across messages."""
    key = _config_key()
    if key not in _cache:
        name = os.getenv("BRAIN", "none").strip().lower()
        _cache[key] = None if name in ("", "none") else build_provider(name)
    return _cache[key]


__all__ = ["get_provider", "build_provider", "LlmProvider", "ToolCall", "KNOWN"]
