"""Keyword tables for the rule-based parser (English first, plus a few romanised Hindi words so the
core still works when a message reaches it untranslated)."""
import re

PLATFORM_NAMES = ["Swiggy", "Zomato", "Uber", "Ola", "Rapido", "Blinkit", "Zepto", "Porter", "Dunzo",
                  "Amazon", "Flipkart", "BigBasket", "inDrive"]
PLATFORM_RE = re.compile(r"\b(" + "|".join(PLATFORM_NAMES) + r")\b", re.I)
_CANON = {n.lower(): n for n in PLATFORM_NAMES}

EXPENSE_CATEGORIES = ["fuel", "repair", "recharge", "emi", "rent", "food", "penalty"]
CATEGORIES = EXPENSE_CATEGORIES + ["other"]
INCOME_CATEGORIES = ["earnings", "incentive"]

# category -> regex of words (earliest match in the text wins)
CATEGORY_WORDS = {
    "fuel": r"petrol|diesel|cng|fuel|refuel\w*|gas|charging|ev charge",
    "repair": r"puncture|repair\w*|servic\w+|mechanic|tyre|tire|oil change|maintenance|spare parts?|garage",
    "recharge": r"recharge\w*|mobile bill|phone bill|data pack|internet",
    "emi": r"emi|instal+ments?|kisht|loan",
    "rent": r"rent|kiraya",
    "food": r"food|tea|chai|lunch|dinner|breakfast|meals?|snacks?|khana|water bottle|coffee",
    "penalty": r"fines?|challan|penalt\w+|deduction|deducted",
}
_CATEGORY_RE = {k: re.compile(r"\b(?:" + v + r")\b", re.I) for k, v in CATEGORY_WORDS.items()}

STRONG_INCOME = re.compile(
    r"\b(earn\w*|received|credited|payouts?|collected|bonus|incentives?|tips?|salary|aaye|aaya|mile|mila|kamaya|kamaye|kamai"
    r"|income|(?:paid|gave|gives|pays?) me|got paid)\b", re.I)
WEAK_INCOME = re.compile(r"\b(got|get|made|from)\b", re.I)
EXPENSE_CUES = re.compile(
    r"\b(spent|spend|spending|paid|pay|bought|buy|filled|fill|put|cost|costs|expenses?|charged|deducted|kharcha|"
    r"dala|dali|diya|diye|bhara|bhare|laga|lage|lagaye|khareeda)\b", re.I)
SAVING_CUES = re.compile(r"\b(saved|saving|savings|save|bachat|bachaye|bachaya|(?:set|kept|put) aside)\b", re.I)
INCENTIVE_RE = re.compile(r"\b(bonus|incentives?|tips?)\b", re.I)

# numbers followed by these are quantities, not money
QUANTITY_UNITS = r"orders?|trips?|rides?|deliver(?:y|ies)|hours?|hrs?|minutes?|mins?|days?|km|kms|kilometers?|kilometres?|litres?|liters?|kg|st|nd|rd|th"

YES_WORDS = {"yes", "yeah", "yep", "yup", "ok", "okay", "correct", "right", "sure", "confirm", "confirmed", "save",
             "done", "haan", "ha", "han", "sahi", "theek", "thik", "ji", "👍", "✓", "✔"}
YES_FILLER = {"it", "is", "that", "thats", "that's", "the", "hai", "please", "this", "its", "it's", "go", "ahead", "and"}
NO_WORDS = {"no", "nope", "wrong", "incorrect", "cancel", "discard", "nahi", "nahin", "galat", "stop", "not", "👎", "✗"}

HELP_RE = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|help|start|menu|what can you do\??|how does this work\??)\W*$", re.I)


def canonical_platform(text: str) -> str | None:
    m = PLATFORM_RE.search(text)
    return _CANON[m.group(1).lower()] if m else None


def find_category(text: str) -> str | None:
    best, best_pos = None, None
    for cat, rx in _CATEGORY_RE.items():
        m = rx.search(text)
        if m and (best_pos is None or m.start() < best_pos):
            best, best_pos = cat, m.start()
    return best
