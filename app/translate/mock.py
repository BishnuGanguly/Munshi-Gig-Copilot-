"""Mock translator: returns the text unchanged (it does NOT translate). For tests and zero-key development."""


class MockTranslate:
    name = "mock"

    def to_english(self, text: str, source_lang: str) -> str:
        return text

    def from_english(self, text: str, target_lang: str) -> str:
        return text
