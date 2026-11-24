"""Sarvam AI speech-to-text (REST).

Confirmed from Sarvam's public docs search results: POST https://api.sarvam.ai/speech-to-text,
multipart form, header `api-subscription-key`, fields `file`, `model`, `language_code`; the
real-time endpoint accepts clips up to ~30 s, so longer audio is split into <=28 s chunks here.

NOT verified (docs were unreachable when this was written): whether `language_code="unknown"`
auto-detects, the exact response field names beyond `transcript` / `language_code`, and which
model is best. Run `python scripts/transcribe_cli.py <file>` with a real key to check; all three
are configurable via env vars (SARVAM_LANGUAGE_CODE, SARVAM_MODEL, SARVAM_MODE).
"""
import io
import logging
import os
import time
import wave

import httpx

from app.errors import SttUnavailableError
from .base import Transcript

log = logging.getLogger("munshi.stt.sarvam")
URL = "https://api.sarvam.ai/speech-to-text"
CHUNK_SECONDS = 28


def split_wav(wav: bytes, chunk_seconds: int = CHUNK_SECONDS) -> list[bytes]:
    """Split a WAV into chunks of at most chunk_seconds."""
    with wave.open(io.BytesIO(wav)) as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        per_chunk = w.getframerate() * chunk_seconds
        chunks = []
        while True:
            frames = w.readframes(per_chunk)
            if not frames:
                break
            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(width)
                out.setframerate(rate)
                out.writeframes(frames)
            chunks.append(buf.getvalue())
    return chunks or [wav]


class SarvamStt:
    name = "sarvam"

    def __init__(self, api_key: str | None = None, model: str | None = None, language_code: str | None = None,
                 mode: str | None = None, retries: int = 2, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key or os.getenv("SARVAM_API_KEY", "")
        self.model = model or os.getenv("SARVAM_MODEL", "saarika:v2.5")
        self.language_code = language_code or os.getenv("SARVAM_LANGUAGE_CODE", "unknown")
        self.mode = mode or os.getenv("SARVAM_MODE") or None
        self.retries = retries
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def _transcribe_chunk(self, wav: bytes) -> dict:
        if not self.api_key:
            raise SttUnavailableError("SARVAM_API_KEY is not set.")
        data = {"model": self.model, "language_code": self.language_code}
        if self.mode:
            data["mode"] = self.mode
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.post(URL, headers={"api-subscription-key": self.api_key}, data=data,
                                        files={"file": ("audio.wav", wav, "audio/wav")})
            except httpx.HTTPError as exc:  # timeouts, connection errors
                last = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (401, 403):
                    raise SttUnavailableError(f"Sarvam rejected the API key (HTTP {resp.status_code}).")
                if resp.status_code == 400:
                    raise SttUnavailableError(f"Sarvam rejected the request: {resp.text[:300]}")
                last = SttUnavailableError(f"Sarvam HTTP {resp.status_code}: {resp.text[:200]}")
            if attempt < self.retries:
                time.sleep(0.5 * 2 ** attempt)
        raise SttUnavailableError(f"Sarvam request failed after {self.retries + 1} attempts: {last}")

    def transcribe(self, wav_bytes: bytes) -> Transcript:
        texts, language = [], None
        for chunk in split_wav(wav_bytes):
            body = self._transcribe_chunk(chunk)
            text = (body.get("transcript") or "").strip()
            if text:
                texts.append(text)
            language = language or body.get("language_code")
        log.info("sarvam model=%s chunks=%d language=%s", self.model, len(texts), language)
        return Transcript(text=" ".join(texts), language=language or "hi-IN", confidence=1.0)
