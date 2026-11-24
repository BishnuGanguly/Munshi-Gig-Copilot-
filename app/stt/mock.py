"""Mock STT: returns a fixed Hinglish transcript so the pipeline runs with zero cost and no API keys."""
from .base import Transcript


class MockStt:
    def transcribe(self, wav_bytes: bytes) -> Transcript:
        return Transcript(text="aaj swiggy se 1400 aaye aur petrol 250 ka dala", language="hi-IN", confidence=0.9)
