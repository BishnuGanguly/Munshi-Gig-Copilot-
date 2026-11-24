"""Sarvam AI text-to-speech (Bulbul), REST.

From Sarvam's public docs search results: POST https://api.sarvam.ai/text-to-speech, JSON body with
`text`, `target_language_code`, `speaker`, `model` ("bulbul:v2": max 1500 characters per request, speakers
like anushka / manisha / vidya / arya / abhilash / karun / hitesh); header `api-subscription-key`; the response
holds `audios`, a list of base64-encoded WAV strings.

NOT verified (docs were unreachable): whether the language field is `target_language_code` or
`language_code` (one doc example used each), so on HTTP 400/422 the request is retried once with the other
name. Speaker names differ for bulbul:v3: set SARVAM_TTS_MODEL / SARVAM_TTS_SPEAKER together.
"""
import base64
import binascii
import logging
import os
import time

import httpx

from app.audio import concat_wavs
from app.errors import AudioError, TtsUnavailableError
from app.translate.sarvam import split_text

log = logging.getLogger("munshi.tts.sarvam")
URL = "https://api.sarvam.ai/text-to-speech"
MAX_CHARS = 1400  # bulbul:v2 limit is 1500


class SarvamTts:
    name = "sarvam"

    def __init__(self, api_key: str | None = None, model: str | None = None, speaker: str | None = None,
                 retries: int = 2, timeout: float = 30.0, transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key or os.getenv("SARVAM_API_KEY", "")
        self.model = model or os.getenv("SARVAM_TTS_MODEL", "bulbul:v2")
        self.speaker = speaker or os.getenv("SARVAM_TTS_SPEAKER", "anushka")
        self.retries = retries
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def _call(self, text: str, lang: str, lang_key: str) -> list[str]:
        if not self.api_key:
            raise TtsUnavailableError("SARVAM_API_KEY is not set.")
        body = {"text": text, lang_key: lang, "speaker": self.speaker, "model": self.model}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.post(URL, headers={"api-subscription-key": self.api_key}, json=body)
            except httpx.HTTPError as exc:
                last = exc
            else:
                if resp.status_code == 200:
                    try:
                        audios = resp.json()["audios"]
                        if not audios:
                            raise KeyError("audios")
                        return audios
                    except (ValueError, KeyError, TypeError):
                        raise TtsUnavailableError(f"Unexpected Sarvam TTS response: {resp.text[:200]!r}")
                if resp.status_code in (401, 403):
                    raise TtsUnavailableError(f"Sarvam rejected the API key (HTTP {resp.status_code}).")
                if resp.status_code in (400, 422):
                    if lang_key == "target_language_code":  # maybe the field is named differently: try once
                        log.warning("sarvam tts rejected target_language_code (%s); trying language_code", resp.text[:200])
                        return self._call(text, lang, "language_code")
                    raise TtsUnavailableError(f"Sarvam rejected the request: {resp.text[:300]}")
                last = TtsUnavailableError(f"Sarvam HTTP {resp.status_code}: {resp.text[:200]}")
            if attempt < self.retries:
                time.sleep(0.5 * 2 ** attempt)
        raise TtsUnavailableError(f"Sarvam TTS failed after {self.retries + 1} attempts: {last}")

    def synthesize(self, text: str, lang: str) -> bytes:
        wavs: list[bytes] = []
        for piece in split_text(text, MAX_CHARS):
            for item in self._call(piece, lang, "target_language_code"):
                try:
                    wavs.append(base64.b64decode(item, validate=False))
                except (binascii.Error, ValueError) as exc:
                    raise TtsUnavailableError("Sarvam returned audio that is not valid base64.") from exc
        try:
            return concat_wavs(wavs)
        except AudioError as exc:
            raise TtsUnavailableError("Sarvam returned audio that could not be decoded.") from exc
