"""Speech-to-text interface."""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Transcript:
    text: str
    language: str          # BCP-47 style code, e.g. "hi-IN", "en-IN"
    confidence: float = 1.0  # 0..1; providers that report none use 1.0


class SttProvider(Protocol):
    name: str

    def transcribe(self, wav_bytes: bytes) -> Transcript: ...
