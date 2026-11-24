"""Rule-based English parser: log entries, corrections, queries and yes/no. Pure functions, no I/O.

Entries are dicts: {"type": income|expense|saving|None, "amount": float|None, "category": str,
"platform": str|None, "date": ISO string}. A missing type or amount is reported by `problems`, never guessed.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import vocab

MIN_AMOUNT, MAX_AMOUNT = 1, 50_000

_NUM_RE = re.compile(
    r"(?P<cur>₹|rs\.?|inr)?\s*(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<mult>k\b|thousand\b|hundred\b|lakhs?\b)?",
    re.I)
_QTY_AFTER = re.compile(r"\s*(?:" + vocab.QUANTITY_UNITS + r")\b", re.I)
_CUR_AFTER = re.compile(r"\s*(?:rupees?|rupaye|rupaya|rs\b|inr\b|/-)", re.I)
_MULT = {"k": 1000, "thousand": 1000, "hundred": 100, "lakh": 100_000, "lakhs": 100_000}
_SPLIT = re.compile(r"(?<!\d),|,(?!\d)|;|\n|\band\b|\bthen\b|\balso\b|\baur\b|\bphir\b|\bplus\b|\.(?!\d)", re.I)


def to_number(s: str) -> float:
    return float(s.replace(",", ""))


def find_amount(clause: str) -> float | None:
    """The money amount in a clause: skips quantities (5 litres, 3 orders, 12th), prefers a currency marker."""
    candidates = []
    for m in _NUM_RE.finditer(clause):
        if _QTY_AFTER.match(clause, m.end()):
            continue
        value = to_number(m.group("num"))
        if m.group("mult"):
            value *= _MULT[m.group("mult").lower()]
        has_cur = bool(m.group("cur")) or bool(_CUR_AFTER.match(clause, m.end()))
        candidates.append((has_cur, value))
    if not candidates:
        return None
    for has_cur, value in candidates:
        if has_cur:
            return value
    return candidates[0][1]


# ------------------------------------------------------------------ dates
_DAY_CUES = [
    (re.compile(r"\bday before yesterday\b|\bparso\b", re.I), -2),
    (re.compile(r"\byesterday\b|\bkal\b", re.I), -1),
    (re.compile(r"\btoday\b|\baaj\b|\btonight\b|\bthis (?:morning|afternoon|evening)\b", re.I), 0),
]


def find_day_offset(text: str) -> int | None:
    for rx, off in _DAY_CUES:
        if rx.search(text):
            return off
    return None


# ------------------------------------------------------------------ logging
@dataclass
class LogParse:
    entries: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _clause_type(clause: str, category: str | None, platform: str | None) -> str | None:
    if vocab.SAVING_CUES.search(clause):
        return "saving"
    if vocab.STRONG_INCOME.search(clause):
        return "income"
    if vocab.EXPENSE_CUES.search(clause) or category in vocab.EXPENSE_CATEGORIES:
        return "expense"
    if vocab.WEAK_INCOME.search(clause) or platform:
        return "income"
    return None


def parse_log(text: str, today: date) -> LogParse:
    message_day = find_day_offset(text)
    entries: list[dict] = []
    prev_type: str | None = None
    for clause in _SPLIT.split(text):
        clause = clause.strip()
        amount = find_amount(clause) if clause else None
        if amount is None:
            continue
        platform = vocab.canonical_platform(clause)
        category = vocab.find_category(clause)
        etype = _clause_type(clause, category, platform) or prev_type
        prev_type = etype or prev_type
        if etype == "income":
            category = "incentive" if vocab.INCENTIVE_RE.search(clause) else "earnings"
        elif etype == "saving":
            category = "savings"
        else:
            category = category or "other"
        offset = find_day_offset(clause)
        if offset is None:
            offset = message_day if message_day is not None else 0
        entries.append({"type": etype, "amount": amount, "category": category, "platform": platform,
                        "date": (today + timedelta(days=offset)).isoformat()})
    result = LogParse(entries)
    for e in entries:
        problems = validate_entry(e)
        if problems:
            result.problems.extend(problems)
    return result


def validate_entry(e: dict) -> list[str]:
    problems = []
    if e["amount"] is None or not (MIN_AMOUNT <= e["amount"] <= MAX_AMOUNT):
        problems.append("amount_out_of_range")
    if e["type"] not in ("income", "expense", "saving"):
        problems.append("needs_type")
    elif e["type"] == "expense" and e["category"] not in vocab.CATEGORIES:
        problems.append("bad_category")
    return problems


def find_type_cue(text: str) -> str | None:
    """A reply to 'income or expense?': returns 'income' / 'expense' / 'saving' / None."""
    t = text.lower()
    if vocab.SAVING_CUES.search(t):
        return "saving"
    if re.search(r"\b(income|earning|earnings|earned|got|received|aaye|mile|kamaya)\b", t):
        return "income"
    if re.search(r"\b(expense|expenses|spent|spending|paid|kharcha|cost)\b", t):
        return "expense"
    return None


# ------------------------------------------------------------------ yes / no
def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w👍👎✓✔✗']+", text.lower())


def is_yes(text: str) -> bool:
    toks = _tokens(text)
    if not toks or any(c.isdigit() for c in text) or len(toks) > 6:
        return False
    if any(t in vocab.NO_WORDS for t in toks):
        return False
    return any(t in vocab.YES_WORDS for t in toks) and all(t in vocab.YES_WORDS | vocab.YES_FILLER for t in toks)


def is_no(text: str) -> bool:
    toks = _tokens(text)
    if not toks or any(c.isdigit() for c in text) or len(toks) > 6:
        return False
    return any(t in vocab.NO_WORDS for t in toks) and not find_type_cue(text)


# ------------------------------------------------------------------ corrections
_N = r"(?:₹|rs\.?\s*)?(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)"
_P_HINDI = re.compile(_N + r"\s*(?:nahi|nahin)\s*,?\s*(?:it was|woh|wo)?\s*" + _N, re.I)           # "250 nahi 300"
_P_NOT_FIRST = re.compile(r"\bnot\s+" + _N + r"\s*[,;]?\s*(?:but|it was|it's|its)?\s*" + _N, re.I)  # "not 250, 300"
_P_NOT_AFTER = re.compile(_N + r"\s*[,;]?\s*(?:but\s+)?not\s+" + _N, re.I)                          # "300 not 250"
_P_SINGLE = re.compile(
    r"(?:\bshould be|\bmake it|\bchange\w*(?: it| that| this| amount)? to|\bit was|\bit's|\bits|\bactually|\bcorrection|"
    r"\bwrong|\bmistake|\bgalti ho gayi|\bgalti|^\s*no)\W+(?:it was\W+)?" + _N, re.I)
_STRONG_CORR = re.compile(
    r"\b(should be|make it|change\w*|it was|actually|correction|wrong|mistake|galti|nahi|nahin|typo|instead)\b|^\s*no\b", re.I)
_NOT_PHRASE = re.compile(r"\bnot\s+\w+(?:\s+\w+)?", re.I)


@dataclass
class Correction:
    new_amount: float | None = None
    old_amount: float | None = None
    category: str | None = None
    platform: str | None = None
    type: str | None = None


def parse_correction(text: str) -> Correction | None:
    c = Correction()
    for rx, order in ((_P_HINDI, "old_new"), (_P_NOT_FIRST, "old_new"), (_P_NOT_AFTER, "new_old")):
        m = rx.search(text)
        if m:
            a, b = to_number(m.group(1)), to_number(m.group(2))
            c.old_amount, c.new_amount = (a, b) if order == "old_new" else (b, a)
            break
    strong = bool(_STRONG_CORR.search(text))
    if c.new_amount is None and strong:
        m = _P_SINGLE.search(text)
        if m:
            c.new_amount = to_number(m.group(1))
    if strong:
        kept = _NOT_PHRASE.sub("", text)
        c.category = vocab.find_category(kept)
        c.platform = vocab.canonical_platform(kept)
        tcue = re.search(r"\b(income|earning|earnings|earned)\b|\b(expense|expenses|expenditure|spent|spending)\b", kept, re.I)
        if tcue:
            c.type = "income" if tcue.group(1) else "expense"
    if c.new_amount is None and not (c.category or c.platform or c.type):
        return None
    if c.new_amount is None and not strong:
        return None
    return c


def is_delete(text: str) -> bool:
    return bool(re.search(r"\b(delete|remove|undo|erase)\b|\bcancel (?:the )?(?:last|previous|latest)\b", text, re.I))


# ------------------------------------------------------------------ queries
@dataclass
class Query:
    kind: str                 # earnings | spending | profit | best_platform | summary | savings | hours | goals
    period: str = "today"     # today | yesterday | this_week | last_week | this_month | last_month | all
    category: str | None = None
    platform: str | None = None


_QUESTION = re.compile(r"\b(how|what|which|show|tell|give|total|summary|report|overview|status|kitna|kitne|kitni|batao|bata)\b|\?", re.I)
_PERIODS = [
    ("last_week", r"last week|previous week|pichle hafte|pichhle hafte"),
    ("last_month", r"last month|previous month|pichle mahine|pichhle mahine"),
    ("this_week", r"this week|\bweek\b|is hafte|\bhafte\b|\bhafta\b"),
    ("this_month", r"this month|\bmonth\b|is mahine|\bmahine\b"),
    ("yesterday", r"yesterday|\bkal\b"),
    ("all", r"all time|overall|so far|till now|in total|ever"),
    ("today", r"today|\baaj\b"),
]


def parse_query(text: str) -> Query | None:
    t = text.lower()
    if not _QUESTION.search(t):
        return None
    if re.search(r"\b(hours?|hourly|per hour)\b", t):
        kind = "hours"
    elif re.search(r"\bgoals?\b", t):
        kind = "goals"
    elif re.search(r"(best|top|most|better).*(platform|app|company)|which (platform|app|company)|\bplatform\b", t):
        kind = "best_platform"
    elif re.search(r"\b(profit|net|take.?home|bacha|left)\b", t):
        kind = "profit"
    elif re.search(r"\b(save|saved|saving|savings|bachat)\b", t):
        kind = "savings"
    elif re.search(r"\b(spent|spend|spending|expenses?|expenditure|kharcha|cost|costs)\b", t):
        kind = "spending"
    elif re.search(r"\b(earn\w*|made|make|income|kamaya|kamai|collect\w*|got|received)\b", t):
        kind = "earnings"
    elif re.search(r"\b(summary|report|overview|status|how (?:did|am) i)\b", t):
        kind = "summary"
    else:
        return None
    period = "this_week" if kind == "best_platform" else "today"
    for key, pat in _PERIODS:
        if re.search(pat, t):
            period = key
            break
    return Query(kind, period, vocab.find_category(t), vocab.canonical_platform(t))


# ------------------------------------------------------------------ daily income target
TARGET_MIN, TARGET_MAX = 100, 50_000
_EVERY_DAY = re.compile(r"\b(?:a|per|every|each)\s+day\b|\bdaily\b", re.I)
_WANT_TO_EARN = re.compile(r"\b(?:want|aim|plan|hope|need|trying)\w*\s+to\s+(?:earn|make)\b", re.I)


_SET_VERB = re.compile(r"\b(set|make|change|update|keep|fix|increase|decrease|raise|lower|reduce)\b", re.I)
_TARGET_PHRASE = re.compile(r"\btarget\b\s*(?:is|to|of|at|=|:|should be|will be)?\s*(?:₹|rs\.?\s*)?\d|\b(?:daily|income) target\b", re.I)
_PROGRESS_WORDS = re.compile(r"\b(progress|status|reach\w*|achiev\w*|hit|met|left|remaining|on track|far|close)\b", re.I)


def _looks_like_log(text: str) -> bool:
    """A message that reports money (got / spent ...) is a log, even if it mentions the target."""
    return find_amount(text) is not None and bool(
        vocab.STRONG_INCOME.search(text) or vocab.EXPENSE_CUES.search(text) or re.search(r"\bgot\b", text, re.I))


def parse_target_set(text: str) -> float | None:
    """'set my daily target to 1500', 'my target is 1500', 'I want to earn 1500 a day' -> 1500.
    Questions ('did I reach my target of 1500?') and money reports ('got 1500, target reached') are not settings."""
    amount = find_amount(text)
    if amount is None:
        return None
    if re.search(r"\btarget\b", text, re.I) and not _QUESTION.search(text) and (
            _SET_VERB.search(text) or _TARGET_PHRASE.search(text)) and not (_looks_like_log(text) and not _SET_VERB.search(text)):
        return amount
    if _WANT_TO_EARN.search(text) and _EVERY_DAY.search(text):
        return amount
    return None


def parse_target_query(text: str) -> str | None:
    """A question about the target ('how far am I from my target', 'did I hit my target yesterday').
    Returns 'today' or 'yesterday', or None when the message is not about the target."""
    if not re.search(r"\btarget\b", text, re.I) or parse_target_set(text) is not None or _looks_like_log(text):
        return None
    if not (_QUESTION.search(text) or _PROGRESS_WORDS.search(text)):
        return None
    return "yesterday" if re.search(r"\byesterday\b|\bkal\b", text, re.I) else "today"
