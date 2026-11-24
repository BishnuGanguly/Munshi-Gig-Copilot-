"""LanguageService: normalise -> translate with a provider chain -> verify numbers survived."""
import logging

from app.errors import TranslateUnavailableError
from .base import TranslateProvider
from .languages import is_english
from .normalize import normalize, numbers_preserved
from .numbers import normalize_digits

log = logging.getLogger("munshi.translate")


class LanguageService:
    def __init__(self, providers: list[TranslateProvider]) -> None:
        self.providers = providers

    def normalize(self, text: str) -> str:
        return normalize(text)

    def to_english(self, text: str, lang: str) -> tuple[str, list[str]]:
        """Return (english_text, warnings). `text` should already be normalised."""
        if is_english(lang):
            return text, []
        best: tuple[str, list[str]] | None = None
        errors: list[str] = []
        for provider in self.providers:
            try:
                out = provider.to_english(text, lang).strip()
            except TranslateUnavailableError as exc:
                log.warning("translate provider=%s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                continue
            ok, missing = numbers_preserved(text, out)
            if ok:
                return normalize_digits(out), []
            log.warning("translate provider=%s dropped numbers %s", provider.name, missing)
            if best is None:
                best = (normalize_digits(out), [f"numbers_not_preserved: {', '.join(missing)}"])
        if best:
            return best
        raise TranslateUnavailableError("; ".join(errors) or "No translation provider configured")

    def from_english(self, text: str, lang: str) -> tuple[str, list[str]]:
        """Return (text_in_user_language, warnings). Never raises: on failure the English reply is returned."""
        if is_english(lang):
            return text, []
        warnings: list[str] = []
        for provider in self.providers:
            try:
                out = provider.from_english(text, lang).strip()
            except TranslateUnavailableError as exc:
                log.warning("translate provider=%s failed: %s", provider.name, exc)
                warnings.append(f"reply_translation_failed[{provider.name}]")
                continue
            ok, missing = numbers_preserved(text, out)
            if ok:
                return normalize_digits(out), []
            warnings.append(f"reply_numbers_not_preserved[{provider.name}]: {', '.join(missing)}")
        return text, warnings + ["reply_not_translated"]
