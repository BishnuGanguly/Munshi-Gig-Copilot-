"""Mock translator: passes text through unchanged (it does NOT translate). Placeholder for Step 3."""


class MockTranslate:
    def to_english(self, text: str, source_lang: str) -> str:
        return text

    def from_english(self, text: str, target_lang: str) -> str:
        return text
