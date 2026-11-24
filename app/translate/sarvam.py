"""Sarvam AI translation (REST).

From Sarvam's public docs search results: POST https://api.sarvam.ai/translate, JSON body with
`input` (max 2000 chars), `source_language_code` (or "auto"), `target_language_code`, `model`
("sarvam-translate:v1" or "mayura:v1"); header `api-subscription-key`; response field `translated_text`.

NOT verified (docs were unreachable when written): the allowed values of the optional fields
`numerals_format` and `output_script`. They are sent as configured, and if Sarvam answers HTTP 400 the
request is retried once WITHOUT them, so a wrong guess degrades gracefully.
"""
import logging
import os
import re
import time

import httpx

from app.errors import TranslateUnavailableError
from .languages import HINGLISH, is_english

log = logging.getLogger("munshi.translate.sarvam")
URL = "https://api.sarvam.ai/translate"
MAX_CHARS = 1800  # Sarvam's limit is 2000 characters per request


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split on sentence boundaries so each piece fits one request."""
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        while len(sentence) > limit:  # a single endless sentence: hard split
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if len(current) + len(sentence) + 1 > limit and current:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _api_code(lang: str) -> str:
    return "hi-IN" if lang == HINGLISH else lang


class SarvamTranslate:
    name = "sarvam"

    def __init__(self, api_key: str | None = None, model: str | None = None, retries: int = 2,
                 timeout: float = 20.0, numerals_format: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key or os.getenv("SARVAM_API_KEY", "")
        self.model = model or os.getenv("SARVAM_TRANSLATE_MODEL", "sarvam-translate:v1")
        env_nf = os.getenv("SARVAM_NUMERALS_FORMAT", "international")
        self.numerals_format = numerals_format if numerals_format is not None else env_nf
        self.retries = retries
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def _call(self, text: str, source: str, target: str, extra: dict) -> str:
        if not self.api_key:
            raise TranslateUnavailableError("SARVAM_API_KEY is not set.")
        body = {"input": text, "source_language_code": source, "target_language_code": target,
                "model": self.model, **extra}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.post(URL, headers={"api-subscription-key": self.api_key}, json=body)
            except httpx.HTTPError as exc:
                last = exc
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()["translated_text"]
                    except (ValueError, KeyError):
                        raise TranslateUnavailableError(f"Unexpected Sarvam response: {resp.text[:200]!r}")
                if resp.status_code in (401, 403):
                    raise TranslateUnavailableError(f"Sarvam rejected the API key (HTTP {resp.status_code}).")
                if resp.status_code in (400, 422):
                    if extra:  # maybe an optional field value is wrong: retry once without them
                        log.warning("sarvam translate rejected optional fields (%s); retrying without", resp.text[:200])
                        return self._call(text, source, target, {})
                    raise TranslateUnavailableError(f"Sarvam rejected the request: {resp.text[:300]}")
                last = TranslateUnavailableError(f"Sarvam HTTP {resp.status_code}: {resp.text[:200]}")
            if attempt < self.retries:
                time.sleep(0.5 * 2 ** attempt)
        raise TranslateUnavailableError(f"Sarvam translate failed after {self.retries + 1} attempts: {last}")

    def _translate(self, text: str, source: str, target: str, extra: dict) -> str:
        return " ".join(self._call(piece, source, target, extra) for piece in split_text(text))

    def to_english(self, text: str, source_lang: str) -> str:
        extra = {"numerals_format": self.numerals_format} if self.numerals_format else {}
        return self._translate(text, _api_code(source_lang), "en-IN", extra)

    def from_english(self, text: str, target_lang: str) -> str:
        extra = {"numerals_format": self.numerals_format} if self.numerals_format else {}
        if target_lang == HINGLISH:
            extra["output_script"] = os.getenv("SARVAM_ROMAN_SCRIPT", "roman")  # UNVERIFIED value
        return self._translate(text, "en-IN", _api_code(target_lang), extra)
