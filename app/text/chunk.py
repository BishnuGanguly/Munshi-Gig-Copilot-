"""Splitting long text into pieces a provider will accept."""
import re


def split_text(text: str, limit: int) -> list[str]:
    """Split on sentence boundaries so each piece fits `limit` characters."""
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        while len(sentence) > limit:           # a single endless sentence: hard split
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if len(current) + len(sentence) + 1 > limit and current:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces
