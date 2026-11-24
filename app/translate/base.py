"""Translation interface. Internal language is always English; real providers arrive in Step 3."""
from typing import Protocol


class TranslateProvider(Protocol):
    def to_english(self, text: str, source_lang: str) -> str: ...
    def from_english(self, text: str, target_lang: str) -> str: ...
