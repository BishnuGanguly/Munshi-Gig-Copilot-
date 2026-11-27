"""Text-to-speech interface. Providers return a WAV (any sample rate); the service converts to OGG/Opus."""
from typing import Protocol


class TtsProvider(Protocol):
    name: str

    def synthesize(self, text: str, lang: str) -> bytes: ...
