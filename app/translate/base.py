"""Translation provider interface. Internal language is always English."""
from typing import Protocol


class TranslateProvider(Protocol):
    name: str

    def to_english(self, text: str, source_lang: str) -> str: ...
    def from_english(self, text: str, target_lang: str) -> str: ...
