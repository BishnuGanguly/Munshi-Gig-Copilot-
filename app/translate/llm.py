"""Optional LLM translation using Claude (pay-per-use; no free tier). Install: `uv sync --extra llm`.
Good at Hinglish and code-mixed text, which dedicated translators sometimes struggle with.
Set ANTHROPIC_API_KEY. Model defaults to claude-haiku-5-5 (override with LLM_TRANSLATE_MODEL)."""
import os

from app.errors import TranslateUnavailableError
from .languages import HINGLISH, LANGUAGE_NAMES

_SYSTEM = (
    "You translate short messages for a bookkeeping assistant used by Indian delivery and ride-hailing riders. "
    "Translate faithfully from {src} to {dst}. Keep every number as digits, exactly as given. Keep platform names "
    "(Swiggy, Zomato, Uber, Ola, Rapido, Blinkit, Zepto) and rupee amounts unchanged. Do not answer, explain, "
    "or add anything. Output only the translation."
)


class LlmTranslate:
    name = "llm"

    def __init__(self, client=None, model: str | None = None) -> None:
        self.model = model or os.getenv("LLM_TRANSLATE_MODEL", "claude-haiku-5-5")
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise TranslateUnavailableError("The `anthropic` package is not installed (uv sync --extra llm).") from exc
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise TranslateUnavailableError("ANTHROPIC_API_KEY is not set.")
            self._client = anthropic.Anthropic()
        return self._client

    def _run(self, text: str, src: str, dst: str) -> str:
        client = self._get_client()
        try:
            resp = client.messages.create(
                model=self.model, max_tokens=1024,
                system=_SYSTEM.format(src=src, dst=dst),
                messages=[{"role": "user", "content": text}],
            )
        except Exception as exc:  # network, auth, rate limit: all mean "this provider is unavailable"
            raise TranslateUnavailableError(f"LLM translation failed: {type(exc).__name__}: {exc}") from exc
        if getattr(resp, "stop_reason", None) == "refusal":
            raise TranslateUnavailableError("The model declined to translate this text.")
        out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not out:
            raise TranslateUnavailableError("The model returned an empty translation.")
        return out

    def to_english(self, text: str, source_lang: str) -> str:
        return self._run(text, LANGUAGE_NAMES.get(source_lang, source_lang), "English")

    def from_english(self, text: str, target_lang: str) -> str:
        dst = LANGUAGE_NAMES.get(target_lang, target_lang)
        if target_lang == HINGLISH:
            dst += ", the way Indians type on WhatsApp: simple, English words kept in English"
        return self._run(text, "English", dst)
