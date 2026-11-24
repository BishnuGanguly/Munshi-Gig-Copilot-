"""Provider selection and fallback chain.

STT_PROVIDER=sarvam|faster_whisper|mock        primary engine
STT_FALLBACK=faster_whisper|sarvam|mock|none   tried when the primary fails (default none)
STT_MIN_CONFIDENCE=0.0..1.0                    below this a transcript is treated as unclear
"""
import logging
import os

from app.errors import SttUnavailableError, UnclearAudioError
from .base import SttProvider, Transcript
from .mock import MockStt

log = logging.getLogger("munshi.stt")


def _build(name: str) -> SttProvider:
    if name == "mock":
        return MockStt()
    if name == "sarvam":
        from .sarvam import SarvamStt
        return SarvamStt()
    if name == "faster_whisper":
        from .whisper_local import WhisperStt
        return WhisperStt()
    raise ValueError(f"Unknown STT provider: {name!r} (use sarvam, faster_whisper or mock)")


class FallbackStt:
    name = "chain"

    def __init__(self, providers: list[SttProvider], min_confidence: float) -> None:
        self.providers = providers
        self.min_confidence = min_confidence

    def transcribe(self, wav_bytes: bytes) -> Transcript:
        errors: list[str] = []
        unclear = False
        for provider in self.providers:
            try:
                tr = provider.transcribe(wav_bytes)
            except SttUnavailableError as exc:
                log.warning("stt provider=%s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
                continue
            if not tr.text.strip() or tr.confidence < self.min_confidence:
                log.warning("stt provider=%s unclear (text=%r confidence=%.2f)", provider.name, tr.text, tr.confidence)
                unclear = True
                continue
            return tr
        if unclear:
            raise UnclearAudioError("I could not understand the audio. Please speak clearly and try again.")
        raise SttUnavailableError("; ".join(errors) or "No speech-to-text provider configured")


def get_stt() -> SttProvider:
    primary = os.getenv("STT_PROVIDER", "mock")
    fallback = os.getenv("STT_FALLBACK", "none")
    providers = [_build(primary)]
    if fallback and fallback != "none" and fallback != primary:
        providers.append(_build(fallback))
    return FallbackStt(providers, float(os.getenv("STT_MIN_CONFIDENCE", "0.3")))


__all__ = ["get_stt", "SttProvider", "Transcript"]
