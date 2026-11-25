"""The small, deterministic rules that stay in code when understanding moves to the model.

Nothing here parses natural language. It is: the limits an amount must satisfy, the values an entry's
fields are checked against, the shape of a query, and yes/no - the commonest message in the app, which is
answered locally so it costs nothing and cannot be got wrong.
"""
import re
from dataclasses import dataclass

# Amounts a rider can log, and the daily income target they can set.
MIN_AMOUNT, MAX_AMOUNT = 1, 50_000
TARGET_MIN, TARGET_MAX = 100, 50_000

EXPENSE_CATEGORIES = ["fuel", "repair", "recharge", "emi", "rent", "food", "penalty"]
INCOME_CATEGORIES = ["earnings", "incentive"]
CATEGORIES = EXPENSE_CATEGORIES + ["other"]
ALL_CATEGORIES = CATEGORIES + INCOME_CATEGORIES + ["savings"]

PLATFORMS = ["Swiggy", "Zomato", "Uber", "Ola", "Rapido", "Blinkit", "Zepto", "Porter", "Dunzo", "Amazon",
             "Flipkart", "BigBasket", "inDrive"]


@dataclass
class Query:
    """What `query_summary` was asked for."""
    kind: str                 # earnings | spending | profit | best_platform | summary | savings | hours | goals
    period: str = "today"     # today | yesterday | this_week | last_week | this_month | last_month | all
    category: str | None = None
    platform: str | None = None


def validate_entry(e: dict) -> list[str]:
    """Problems with one entry, whoever produced it. An empty list means it is safe to store."""
    problems = []
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
    """A bare refusal. 'no, it was income' is not one: it carries a correction for the model to read."""
    toks = _tokens(text)
    if not toks or any(c.isdigit() for c in text) or len(toks) > 6:
        return False
    if not any(t in NO_WORDS for t in toks):
        return False
    return not re.search(r"\b(income|earning|earnings|earned|expense|expenses|spent|saving|savings)\b", text, re.I)
