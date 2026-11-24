"""Language codes and detection for text input.

Codes follow BCP-47 as Sarvam uses them (hi-IN, ta-IN ...). `hi-Latn` is our own tag for Hinglish:
Hindi written in Latin script, which is how many riders type on WhatsApp.
"""
import re

HINGLISH = "hi-Latn"
DEFAULT_LANGUAGE = "en-IN"

LANGUAGE_NAMES = {
    "en-IN": "English", "hi-IN": "Hindi", HINGLISH: "Hinglish (Hindi written in Roman script)",
    "bn-IN": "Bengali", "gu-IN": "Gujarati", "kn-IN": "Kannada", "ml-IN": "Malayalam",
    "mr-IN": "Marathi", "od-IN": "Odia", "pa-IN": "Punjabi", "ta-IN": "Tamil", "te-IN": "Telugu",
}

_SCRIPTS = [  # (first, last, language)
    (0x0900, 0x097F, "hi-IN"), (0x0980, 0x09FF, "bn-IN"), (0x0A00, 0x0A7F, "pa-IN"),
    (0x0A80, 0x0AFF, "gu-IN"), (0x0B00, 0x0B7F, "od-IN"), (0x0B80, 0x0BFF, "ta-IN"),
    (0x0C00, 0x0C7F, "te-IN"), (0x0C80, 0x0CFF, "kn-IN"), (0x0D00, 0x0D7F, "ml-IN"),
]

# Romanised Hindi words that are not English. Used only to tell Hinglish from English.
_HINGLISH_MARKERS = {
    "aaj", "aaye", "aaya", "kal", "kitna", "kitne", "kitni", "kamaya", "kamaye", "hai", "hain", "nahi", "nahin",
    "mein", "ka", "ki", "ke", "se", "ko", "aur", "dala", "diya", "liya", "mile", "mila", "kharcha", "bacha",
    "batao", "bata", "hafte", "hafta", "mahine", "mahina", "paise", "rupaye", "galti", "tha", "thi", "raha",
    "gaya", "gayi", "hua", "hui", "hazaar", "hazar", "sau", "kya", "kaun", "sabse", "achha", "accha", "mera",
    "meri", "mujhe", "bhai", "lage", "laga", "bhara", "kamai", "karwaya", "kiya", "dedh", "dhai", "saadhe",
}


def normalize_code(code: str | None) -> str | None:
    """'hi-in' -> 'hi-IN'; bare 'hi' -> 'hi-IN'; unknown values -> None."""
    if not code:
        return None
    c = code.strip().replace("_", "-")
    if c.lower() == HINGLISH.lower():
        return HINGLISH
    base = c.split("-")[0].lower()
    full = f"{base}-IN"
    return full if full in LANGUAGE_NAMES else None


def is_english(code: str | None) -> bool:
    return bool(code) and code.lower().startswith("en")


def detect_text_language(text: str, last_language: str | None = None) -> str:
    """Guess the language of typed text from its script, falling back to the user's last language."""
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        cp = ord(ch)
        for lo, hi, lang in _SCRIPTS:
            if lo <= cp <= hi:
                counts[lang] = counts.get(lang, 0) + 1
                break
        else:
            if ch.isascii() and ch.isalpha():
                latin += 1
    if counts:
        lang = max(counts, key=counts.get)
        if lang == "hi-IN" and last_language == "mr-IN":  # Devanagari is shared by Hindi and Marathi
            return "mr-IN"
        return lang
    if latin == 0:
        return normalize_code(last_language) or DEFAULT_LANGUAGE
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(1 for w in words if w in _HINGLISH_MARKERS)
    if hits >= 2 or (words and hits / len(words) >= 0.2):
        return HINGLISH
    if hits == 1 and last_language == HINGLISH:
        return HINGLISH
    return "en-IN"
