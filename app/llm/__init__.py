"""Model providers, chosen by configuration.

    BRAIN=sarvam                 one provider
    BRAIN=sarvam,anthropic       a chain: the second answers only when the first is down
    BRAIN=none                   off (the assistant says it did not understand)

Names: anthropic | sarvam | openai | groq | openrouter | ollama | openai_compat (anything else that speaks
the OpenAI-style protocol - set BRAIN_BASE_URL, BRAIN_API_KEY_ENV, BRAIN_AUTH and BRAIN_MODEL_OPENAI_COMPAT).

Per provider you can override: BRAIN_MODEL_<NAME> and BRAIN_BASE_URL_<NAME>. `BRAIN_MODEL` is shorthand for the
FIRST provider's model. Adding a provider that speaks the OpenAI protocol is one entry in PRESETS; a provider
with its own protocol is one new class implementing app/llm/base.py.
"""
import os
from dataclasses import dataclass

from .anthropic_provider import AnthropicProvider, DEFAULT_MODEL as ANTHROPIC_MODEL
from .base import LlmProvider, ToolCall
from .chain import FallbackLlm
from .openai_compat import OpenAICompatProvider


@dataclass(frozen=True)
class Preset:
    base_url: str
    key_env: str          # the environment variable holding the key
    auth: str             # bearer | header:<Name> | none
    model: str            # default model
    reasoning: tuple = () # (("choose", "low"), ("complete", "off")): reasoning_effort per kind of call


# From Sarvam's docs, as surfaced by search (not opened directly): POST /v1/chat/completions, the key in an
# `api-subscription-key` header, `sarvam-105b` (sarvam-m is deprecated). The others use the standard protocol.
PRESETS: dict[str, Preset] = {
    # sarvam-105b reasons by default and its reasoning tokens count against max_tokens. Choosing an action gets
    # a little thinking; rewording a sentence gets none (null turns thinking off, per Sarvam's docs).
    "sarvam": Preset("https://api.sarvam.ai/v1", "SARVAM_API_KEY", "header:api-subscription-key", "sarvam-105b",
                     (("choose", "low"), ("complete", "off"))),
    "openai": Preset("https://api.openai.com/v1", "OPENAI_API_KEY", "bearer", "gpt-4.1-mini"),
    "groq": Preset("https://api.groq.com/openai/v1", "GROQ_API_KEY", "bearer", "llama-3.3-70b-versatile"),
    "openrouter": Preset("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "bearer", "openai/gpt-4.1-mini"),
    "ollama": Preset("http://localhost:11434/v1", "OLLAMA_API_KEY", "none", "llama3.1"),
}
KNOWN = sorted({"anthropic", "openai_compat", *PRESETS})


def _model_for(name: str, default: str, primary: bool) -> str:
    return (os.getenv(f"BRAIN_MODEL_{name.upper()}")
            or (os.getenv("BRAIN_MODEL") if primary else None)
            or default)


def build_provider(name: str, primary: bool = True) -> LlmProvider:
    """One provider by name. Cheap: nothing is contacted and no key is needed until it is used."""
    name = name.strip().lower()
    if name == "anthropic":
        return AnthropicProvider(model=_model_for(name, ANTHROPIC_MODEL, primary))
    if name == "openai_compat":
        base = os.getenv("BRAIN_BASE_URL")
        if not base:
            raise ValueError("BRAIN=openai_compat needs BRAIN_BASE_URL (and usually BRAIN_API_KEY_ENV, BRAIN_AUTH)")
        key_env = os.getenv("BRAIN_API_KEY_ENV", "")
        model = _model_for(name, "", primary)
        if not model:
            raise ValueError("BRAIN=openai_compat needs BRAIN_MODEL_OPENAI_COMPAT (or BRAIN_MODEL if it is first)")
        return OpenAICompatProvider(name, base, model, os.getenv(key_env) if key_env else None,
                                    os.getenv("BRAIN_AUTH", "bearer"))
    if name in PRESETS:
        p = PRESETS[name]
        reasoning = dict(p.reasoning)
        override = os.getenv(f"BRAIN_REASONING_{name.upper()}")      # low | medium | high | off | omit, both calls
        if override:
            reasoning = {"choose": override, "complete": override}
        return OpenAICompatProvider(name, os.getenv(f"BRAIN_BASE_URL_{name.upper()}") or p.base_url,
                                    _model_for(name, p.model, primary), os.getenv(p.key_env), p.auth,
                                    reasoning=reasoning)
    raise ValueError(f"Unknown BRAIN provider {name!r}. Choose from: none, {', '.join(KNOWN)}")


def _config_key() -> tuple:
    names = ("BRAIN", "BRAIN_MODEL", "BRAIN_BASE_URL", "BRAIN_API_KEY_ENV", "BRAIN_AUTH")
    extra = tuple(sorted((k, v) for k, v in os.environ.items()
                         if k.startswith(("BRAIN_MODEL_", "BRAIN_BASE_URL_", "BRAIN_REASONING_")) or k.endswith("_API_KEY")))
    return tuple(os.getenv(n) for n in names) + extra


_cache: dict[tuple, LlmProvider | None] = {}


def get_provider() -> LlmProvider | None:
    """The configured provider (or chain), or None when BRAIN=none. Cached while the configuration is
    unchanged, so connections are reused across messages."""
    key = _config_key()
    if key not in _cache:
        spec = [s for s in os.getenv("BRAIN", "none").replace(" ", "").lower().split(",") if s]
        if not spec or spec == ["none"]:
            _cache[key] = None
        else:
            providers = [build_provider(n, primary=(i == 0)) for i, n in enumerate(spec)]
            _cache[key] = providers[0] if len(providers) == 1 else FallbackLlm(providers)
    return _cache[key]


__all__ = ["get_provider", "build_provider", "LlmProvider", "ToolCall", "FallbackLlm", "PRESETS", "KNOWN"]
