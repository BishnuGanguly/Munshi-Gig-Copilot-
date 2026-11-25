"""Text clean-up applied before the model sees a message."""
import re

from .numbers import normalize_digits, words_to_digits

# canonical name -> spellings seen from speech-to-text or typing (Latin matched case-insensitively on word edges)
PLATFORMS: dict[str, list[str]] = {
    "Swiggy": ["स्विगी", "स्विग्गी", "स्विगि", "swiggy", "swigi", "swiggi", "swigy", "sviggy"],
    "Zomato": ["ज़ोमैटो", "जोमैटो", "ज़ोमाटो", "जोमाटो", "zomato", "zomaato", "zomatto", "jomato", "zomto"],
    "Uber": ["उबर", "ऊबर", "uber", "oober"],
    "Ola": ["ओला", "ola"],
    "Rapido": ["रैपिडो", "रेपिडो", "रेपीडो", "रैपीडो", "rapido", "rappido", "rapeedo"],
    "Blinkit": ["ब्लिंकिट", "ब्लिंकइट", "ब्लिनकिट", "blinkit", "blinket", "blinkeet"],
    "Zepto": ["ज़ेप्टो", "जेप्टो", "ज़ेपटो", "zepto", "jepto"],
    "Porter": ["पोर्टर", "porter"],
    "Dunzo": ["डंज़ो", "डंजो", "dunzo", "dunjo"],
    "Amazon": ["अमेज़न", "अमेजन", "amazon"],
    "Flipkart": ["फ्लिपकार्ट", "flipkart"],
    "BigBasket": ["बिगबास्केट", "bigbasket"],
    "inDrive": ["इनड्राइव", "इनडाइव", "indrive"],
}
_PLATFORM_RE = [(canon, re.compile("|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)), re.I))
                for canon, aliases in PLATFORMS.items()]


def canonical_platforms(text: str) -> str:
    """Replace platform spellings with their canonical Latin name so translation can't mangle them."""
    for canon, rx in _PLATFORM_RE:
        # Latin aliases must match whole words; Devanagari aliases may carry suffixes (स्विगी से, स्विगीकडून)
        text = rx.sub(lambda m, c=canon: c if _boundary_ok(text, m) else m.group(), text)
    return text


def _boundary_ok(text: str, m: re.Match) -> bool:
    if not m.group().isascii():
        return True
    before = text[m.start() - 1] if m.start() > 0 else " "
    after = text[m.end()] if m.end() < len(text) else " "
    return not (before.isascii() and before.isalnum()) and not (after.isascii() and after.isalnum())


def normalize(text: str) -> str:
    """digits of any script -> ASCII, spoken amounts -> digits, platform names -> canonical."""
    text = normalize_digits(text)
    text = words_to_digits(text)
    text = canonical_platforms(text)
    return re.sub(r"\s+", " ", text).strip()


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def extract_numbers(text: str) -> list[str]:
    """Numbers in `text` as plain digit strings: '1,400' -> '1400'. Native digits are converted first."""
    return [n.replace(",", "") for n in _NUM.findall(normalize_digits(text))]
