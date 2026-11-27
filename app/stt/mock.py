"""Mock STT: fixed Hinglish transcript. For tests and zero-key development."""
from .base import Transcript


class MockStt:
    name = "mock"

    def transcribe(self, wav_bytes: bytes) -> Transcript:
        return Transcript(text="aaj swiggy se 1400 aaye aur petrol 250 ka dala", language="hi-IN", confidence=0.9)
