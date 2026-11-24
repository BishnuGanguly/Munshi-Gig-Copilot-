"""Speech-to-text interface. Real providers (Sarvam, faster-whisper) arrive in Step 2."""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Transcript:
    text: str
    language: str          # BCP-47 style code, e.g. "hi-IN", "en-IN"
    confidence: float = 1.0


class SttProvider(Protocol):
    def transcribe(self, wav_bytes: bytes) -> Transcript: ...
