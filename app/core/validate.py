"""The small, deterministic rules that stay in code when understanding moves to the model.

Nothing here parses natural language. It is: the limits an amount must satisfy, the values an entry's
fields are checked against, the shape of a query, and yes/no - the commonest message in the app, which is
answered locally so it costs nothing and cannot be got wrong.
"""
import re
from dataclasses import dataclass
from datetime import date

# Amounts a rider can log, and the daily income target they can set.
MIN_AMOUNT, MAX_AMOUNT = 1, 50_000
TARGET_MIN, TARGET_MAX = 100, 50_000

EXPENSE_CATEGORIES = ["fuel", "repair", "recharge", "emi", "rent", "food", "penalty", "commission"]
# Costs that belong to one platform (a fine, the platform's cut or subscription). Others, like fuel, are shared.
PLATFORM_COST_CATEGORIES = ["penalty", "commission"]
INCOME_CATEGORIES = ["earnings", "incentive"]
CATEGORIES = EXPENSE_CATEGORIES + ["other"]
ALL_CATEGORIES = CATEGORIES + INCOME_CATEGORIES + ["savings"]

PLATFORMS = ["Swiggy", "Zomato", "Uber", "Ola", "Rapido", "Blinkit", "Zepto", "Porter", "Dunzo", "Amazon",
             "Flipkart", "BigBasket", "inDrive"]


# How far back an entry may be dated. Further back is far more likely a misheard date than a real one.
MAX_BACKDATE_DAYS = 90


def fit_category(type_: str | None, category: str | None) -> str:
    """A category that is valid for the entry's type: income can only be earnings or incentive, an expense
    only an expense category, a saving only savings. Anything else falls back to the type's default, so a
    mismatch (an expense filed as "earnings", or an income left as "fuel" after a correction) cannot be stored."""
    if type_ == "income":
        return category if category in INCOME_CATEGORIES else "earnings"
    if type_ == "saving":
        return "savings"
    return category if category in CATEGORIES else "other"


def parse_day(value, today: date) -> date | None:
    """The entry's date if it is a real ISO date, not in the future and not implausibly old; else None."""
    if not isinstance(value, str):
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    if d > today or (today - d).days > MAX_BACKDATE_DAYS:
        return None
    return d


@dataclass
class Query:
    """What `query_summary` was asked for."""
    kind: str                 # earnings | spending | profit | platform_breakdown | summary | savings | hours
    period: str = "today"     # today | yesterday | this_week | last_week | this_month | last_month | all
    category: str | None = None
    platform: str | None = None


def validate_entry(e: dict, today: date | None = None) -> list[str]:
    """Problems with one entry, whoever produced it. An empty list means it is safe to store."""
    problems = []
    if today is not None and parse_day(e.get("date"), today) is None:
        problems.append("bad_date")
    if e.get("amount") is None or not (MIN_AMOUNT <= e["amount"] <= MAX_AMOUNT):
        problems.append("amount_out_of_range")
    if e.get("type") not in ("income", "expense", "saving"):
        problems.append("needs_type")
    elif e["type"] == "expense" and e.get("category") not in CATEGORIES:
        problems.append("bad_category")
    return problems


# ------------------------------------------------------------------ yes / no
YES_WORDS = {"yes", "yeah", "yep", "yup", "ok", "okay", "correct", "right", "sure", "confirm", "confirmed",
             "save", "done", "haan", "ha", "han", "sahi", "theek", "thik", "ji", "👍", "✓", "✔"}
YES_FILLER = {"it", "is", "that", "thats", "that's", "the", "hai", "please", "this", "its", "it's", "go",
              "ahead", "and"}
NO_WORDS = {"no", "nope", "wrong", "incorrect", "cancel", "discard", "nahi", "nahin", "galat", "stop", "not",
            "👎", "✗"}
# Words that may sit beside a refusal without changing it: "that is wrong", "ye sahi nahi hai", "not correct".
NO_FILLER = {"it", "is", "that", "thats", "that's", "this", "the", "hai", "ho", "please", "ji", "ye", "yeh", "bilkul",
             "sahi", "theek", "thik", "correct", "right", "and"}

_TOKENS = re.compile(r"[\w👍👎✓✔✗']+")


def _tokens(text: str) -> list[str]:
    return _TOKENS.findall(text.lower())


def is_yes(text: str) -> bool:
    """A bare agreement, nothing else. Anything with a digit or more than a few words is not one."""
    toks = _tokens(text)
    if not toks or any(c.isdigit() for c in text) or len(toks) > 6:
        return False
    if any(t in NO_WORDS for t in toks):
        return False
    return any(t in YES_WORDS for t in toks) and all(t in YES_WORDS | YES_FILLER for t in toks)


def is_no(text: str) -> bool:
    """A bare refusal: nothing but refusal words and filler, like is_yes.

    This must be strict. It runs before the model and, when something is pending, DISCARDS it, so a phrase
    that merely contains a refusal word must not match: "cancel my last entry" is a delete request, "wrong
    platform" is a correction, "kuch samajh nahi aaya" is "I did not understand". All of those belong to the
    model.
    """
    toks = _tokens(text)
    if not toks or any(c.isdigit() for c in text) or len(toks) > 6:
        return False
    if not any(t in NO_WORDS for t in toks):
        return False
    return all(t in NO_WORDS | NO_FILLER for t in toks)
