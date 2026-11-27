"""Action handlers, and the registry that exposes them. Logic only; app/core/brain.py routes to them.

Every number shown to the user is computed here with tools.* (SQL/code), never by a model."""
from datetime import date

from . import dates, replies, tools, validate
from .registry import ActionSpec, Context, Outcome, Registry
from .replies import fmt_money

CATEGORIES = validate.ALL_CATEGORIES
PLATFORMS = validate.PLATFORMS


# ------------------------------------------------------------------ shared helpers
def entry_out(e: dict, status: str) -> dict:
    return {**e, "status": status}


def readback(entries: list[dict], today: date) -> str:
    return "Please confirm: " + "; ".join(replies.describe_entry(e, today) for e in entries) + ". Is that correct?"


def ask_type(entries: list[dict]) -> str:
    missing = [e for e in entries if e["type"] is None]
    if len(missing) == 1:
        return f"Was {fmt_money(missing[0]['amount'])} income or an expense?"
    return "Were these amounts income or expenses? Please tell me for each one."


def _complete(e: dict, today: date) -> dict:
    """Fill defaults so entries from any source (rules or a model) have the same shape."""
    e = dict(e)
    e.setdefault("type", None)
    e.setdefault("platform", None)
    e.setdefault("date", today.isoformat())
    if not e.get("category"):
        e["category"] = {"income": "earnings", "saving": "savings"}.get(e["type"], "other")
    return e


def update_readback(last: dict, fields: dict, today: date) -> str:
    new = {**last, **{("occurred_on" if k == "date" else k): v for k, v in fields.items()}}
    return (f"Change the last entry from {replies.describe_tx(last, today)} to {replies.describe_tx(new, today)}? "
            "Say yes to confirm.")


# ------------------------------------------------------------------ target sentences
def progress_sentence(p: dict) -> str:
    if p["met"]:
        return f"Today {fmt_money(p['income'])} of {fmt_money(p['target'])}. Target reached!"
    return f"Today {fmt_money(p['income'])} of {fmt_money(p['target'])}. {fmt_money(p['remaining'])} to go."


# ------------------------------------------------------------------ log_entries
def _log_run(ctx: Context, args: dict) -> Outcome:
    entries = [_complete(e, ctx.today) for e in args["entries"]]
    problems = [p for e in entries for p in validate.validate_entry(e)]
    if "amount_out_of_range" in problems:
        return Outcome(replies.BAD_AMOUNT, intent="log")
    if "needs_type" in problems:
        tools.set_pending(ctx.conn, ctx.user_id, {"action": "log_entries", "entries": entries, "awaiting": "type"}, ctx.now)
        return Outcome(ask_type(entries), [entry_out(e, "needs_type") for e in entries], "log")
    tools.set_pending(ctx.conn, ctx.user_id, {"action": "log_entries", "entries": entries}, ctx.now)
    return Outcome(readback(entries, ctx.today), [entry_out(e, "pending_confirmation") for e in entries], "log")


def _log_commit(ctx: Context, pending: dict) -> Outcome:
    today = ctx.today
    before = tools.get_summary(ctx.conn, ctx.user_id, today, today)["income"]
    for e in pending["entries"]:
        tools.add_transaction(ctx.conn, ctx.user_id, e["type"], e["amount"], e["category"], e["platform"], e["date"])
    s = tools.get_summary(ctx.conn, ctx.user_id, today, today)
    reply = f"Saved. Today's profit is {fmt_money(s['profit'])}."
    target = tools.get_target(ctx.conn, ctx.user_id, today)
    if target is not None and any(e["type"] == "income" and e["date"] == today.isoformat() for e in pending["entries"]):
        if s["income"] >= target and before < target:
            reply += f" You reached your daily target of {fmt_money(target)}!"
        elif s["income"] >= target:
            reply += f" Target of {fmt_money(target)} is already reached today."
        else:
            reply += f" Target: {fmt_money(s['income'])} of {fmt_money(target)}, {fmt_money(target - s['income'])} to go."
    return Outcome(reply, [entry_out(e, "saved") for e in pending["entries"]], "confirm")


# ------------------------------------------------------------------ correct_entry / delete_entry (the last SAVED entry)
def _correct_run(ctx: Context, args: dict) -> Outcome:
    last = tools.get_last_transaction(ctx.conn, ctx.user_id)
    if not last:
        return Outcome("There is no saved entry to correct.", intent="correct")
    fields = {k: args[k] for k in ("amount", "category", "platform", "type") if args.get(k) is not None}
    if not fields:
        return Outcome("What should I change? For example: it should be 300.", intent="correct")
    if "amount" in fields and not (validate.MIN_AMOUNT <= fields["amount"] <= validate.MAX_AMOUNT):
        return Outcome(replies.BAD_AMOUNT, intent="correct")
    tools.set_pending(ctx.conn, ctx.user_id, {"action": "correct_entry", "tx_id": last["id"], "fields": fields}, ctx.now)
    return Outcome(update_readback(last, fields, ctx.today), intent="correct")


def _correct_commit(ctx: Context, pending: dict) -> Outcome:
    ok = tools.update_transaction(ctx.conn, ctx.user_id, pending["tx_id"], **pending["fields"])
    last = tools.get_last_transaction(ctx.conn, ctx.user_id)
    if not ok or not last:
        return Outcome("I could not find that entry any more.", intent="confirm")
    return Outcome(f"Updated. The last entry is now {replies.describe_tx(last, ctx.today)}.", intent="confirm")


def _delete_run(ctx: Context, args: dict) -> Outcome:
    last = tools.get_last_transaction(ctx.conn, ctx.user_id)
    if not last:
        return Outcome(replies.NO_ENTRIES, intent="delete")
    tools.set_pending(ctx.conn, ctx.user_id, {"action": "delete_entry", "tx_id": last["id"]}, ctx.now)
    return Outcome(f"Delete the last entry ({replies.describe_tx(last, ctx.today)})? Say yes to confirm.", intent="delete")


def _delete_commit(ctx: Context, pending: dict) -> Outcome:
    ok = tools.delete_transaction(ctx.conn, ctx.user_id, pending["tx_id"])
    return Outcome("Deleted." if ok else "I could not find that entry any more.", intent="confirm")


# ------------------------------------------------------------------ query_summary
def _phrase(label: str, rest: str) -> str:
    return f"{label[0].upper()}{label[1:]} {rest}"


def answer_query(ctx: Context, q: validate.Query) -> str:
    if q.kind == "hours":
        return "I do not track working hours yet."
    if q.kind == "goals":
        return "Savings goals are coming soon."
    label, start, end = dates.resolve_period(q.period, ctx.today)
    s = tools.get_summary(ctx.conn, ctx.user_id, start, end, category=q.category if q.kind == "spending" else None,
                          platform=q.platform if q.kind == "earnings" else None)
    if q.kind == "earnings":
        if s["income"] == 0:
            return f"No income recorded {label}."
        text = _phrase(label, f"you earned {fmt_money(s['income'])}")
        text = text.replace("you earned", f"from {q.platform} you earned") if q.platform else text
        top = list(s["income_by_platform"].items())[:3]
        if len(top) >= 2 and not q.platform:
            text += ". " + ", ".join(f"{p} {fmt_money(v)}" for p, v in top)
        return text + "."
    if q.kind == "spending":
        if s["expense"] == 0:
            return f"No expenses recorded {label}."
        on = f" on {q.category}" if q.category else ""
        text = _phrase(label, f"you spent {fmt_money(s['expense'])}{on}")
        if not q.category and s["expense_by_category"]:
            cat, val = next(iter(s["expense_by_category"].items()))
            text += f". Biggest: {cat} {fmt_money(val)}"
        return text + "."
    if q.kind == "savings":
        return f"No savings recorded {label}." if s["saving"] == 0 else _phrase(label, f"you saved {fmt_money(s['saving'])}.")
    if q.kind == "best_platform":
        by = list(s["income_by_platform"].items())
        if not by:
            return f"No income with a platform recorded {label}."
        text = f"Your best platform {label} is {by[0][0]} with {fmt_money(by[0][1])}."
        if len(by) > 1:
            text += f" Next: {by[1][0]} {fmt_money(by[1][1])}."
        return text
    if s["count"] == 0:                                              # profit / summary
        return f"No entries recorded {label}."
    head = f"{label[0].upper()}{label[1:]}"
    text = f"{head}: income {fmt_money(s['income'])}, expenses {fmt_money(s['expense'])}, profit {fmt_money(s['profit'])}."
    if q.kind == "summary" and s["saving"] > 0:
        text += f" You also saved {fmt_money(s['saving'])}."
    return text


def _query_run(ctx: Context, args: dict) -> Outcome:
    q = validate.Query(args["kind"], args.get("period") or "today", args.get("category"), args.get("platform"))
    return Outcome(answer_query(ctx, q), intent="query")


# ------------------------------------------------------------------ set_target / get_target_progress
def _target_range_msg() -> str:
    return f"A daily target must be between {fmt_money(validate.TARGET_MIN)} and {fmt_money(validate.TARGET_MAX)}."


def _target_proposal(ctx: Context, amount: float) -> str:
    current = tools.get_target(ctx.conn, ctx.user_id, ctx.today)
    if current is None:
        return f"Set your daily income target to {fmt_money(amount)}? Say yes to confirm."
    return f"Change your daily income target from {fmt_money(current)} to {fmt_money(amount)}? Say yes to confirm."


def _set_target_run(ctx: Context, args: dict) -> Outcome:
    amount = float(args["amount"])
    if not (validate.TARGET_MIN <= amount <= validate.TARGET_MAX):
        return Outcome(_target_range_msg(), intent="target")
    if tools.get_target(ctx.conn, ctx.user_id, ctx.today) == amount:
        return Outcome(f"Your daily income target is already {fmt_money(amount)}.", intent="target")
    tools.set_pending(ctx.conn, ctx.user_id, {"action": "set_target", "amount": amount}, ctx.now)
    return Outcome(_target_proposal(ctx, amount), intent="target")


def _set_target_commit(ctx: Context, pending: dict) -> Outcome:
    tools.set_target(ctx.conn, ctx.user_id, pending["amount"], ctx.today)
    reply = f"Done. Your daily income target is {fmt_money(pending['amount'])}."
    p = tools.target_progress(ctx.conn, ctx.user_id, ctx.today)
    if p and p["income"] > 0:
        reply += " " + progress_sentence(p)
    return Outcome(reply, intent="confirm")


def _progress_run(ctx: Context, args: dict) -> Outcome:
    day = ctx.today
    if args.get("day") == "yesterday":
        day = date.fromordinal(ctx.today.toordinal() - 1)
    if tools.get_target(ctx.conn, ctx.user_id, ctx.today) is None and tools.get_target(ctx.conn, ctx.user_id, day) is None:
        return Outcome("You have not set a daily target. Say for example: set my daily target to 1500.", intent="target")
    p = tools.target_progress(ctx.conn, ctx.user_id, day)
    if p is None:
        return Outcome("No target was set for that day.", intent="target")
    if day == ctx.today:
        text = progress_sentence(p)
        streak = tools.target_streak(ctx.conn, ctx.user_id, ctx.today)
    else:
        text = f"Yesterday {fmt_money(p['income'])} of {fmt_money(p['target'])}. " + (
            "Target reached." if p["met"] else f"Missed by {fmt_money(p['remaining'])}.")
        streak = 0
    if streak >= 2:
        text += f" Streak: {streak} days."
    return Outcome(text, intent="target")


# ------------------------------------------------------------------ help
def _help_run(ctx: Context, args: dict) -> Outcome:
    return Outcome(replies.HELP, intent="help")


# ------------------------------------------------------------------ registry
_ENTRY = {
    "type": "object",
    "properties": {
        "type": {"type": ["string", "null"], "enum": ["income", "expense", "saving", None],
                 "description": "Omit when the rider did not say whether it was income or an expense"},
        "amount": {"type": "number", "description": "Rupees, between 1 and 50000"},
        "category": {"type": ["string", "null"], "enum": CATEGORIES + [None]},
        "platform": {"type": ["string", "null"], "enum": PLATFORMS + [None]},
        "date": {"type": "string", "description": "ISO date YYYY-MM-DD; today unless the user said otherwise"},
    },
    "required": ["amount"],
    "additionalProperties": False,
}
_QUERY_KINDS = ["earnings", "spending", "profit", "best_platform", "summary", "savings", "hours", "goals"]
_PERIODS = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "all"]


def build_registry() -> Registry:
    r = Registry()
    r.register(ActionSpec(
        "log_entries", "Record income, expenses or savings the user just reported. Needs the user's confirmation before saving.",
        {"type": "object", "properties": {"entries": {"type": "array", "minItems": 1, "maxItems": 10, "items": _ENTRY}},
         "required": ["entries"], "additionalProperties": False},
        _log_run, read_only=False, commit=_log_commit))
    r.register(ActionSpec(
        "correct_entry", "Change the amount, category, platform or type of the most recent SAVED entry. Needs confirmation.",
        {"type": "object", "properties": {
            "amount": {"type": "number"}, "category": {"type": "string", "enum": CATEGORIES},
            "platform": {"type": "string", "enum": PLATFORMS}, "type": {"type": "string", "enum": ["income", "expense", "saving"]}},
         "additionalProperties": False},
        _correct_run, read_only=False, commit=_correct_commit))
    r.register(ActionSpec(
        "delete_entry", "Delete the most recent saved entry. Needs confirmation.",
        {"type": "object", "properties": {}, "additionalProperties": False}, _delete_run, read_only=False, commit=_delete_commit))
    r.register(ActionSpec(
        "query_summary", "Answer a question about the user's own money for a period (earnings, spending, profit, best platform, savings).",
        {"type": "object", "properties": {
            "kind": {"type": "string", "enum": _QUERY_KINDS}, "period": {"type": "string", "enum": _PERIODS},
            "category": {"type": ["string", "null"], "enum": CATEGORIES + [None]},
            "platform": {"type": ["string", "null"], "enum": PLATFORMS + [None]}},
         "required": ["kind"], "additionalProperties": False}, _query_run))
    r.register(ActionSpec(
        "set_target", "Set or change the user's daily INCOME target in rupees (100 to 50000; out-of-range values get a friendly refusal). Needs confirmation.",
        {"type": "object", "properties": {"amount": {"type": "number"}},
         "required": ["amount"], "additionalProperties": False},
        _set_target_run, read_only=False, commit=_set_target_commit))
    r.register(ActionSpec(
        "get_target_progress", "Say how today's (or yesterday's) income compares with the daily target, plus the streak.",
        {"type": "object", "properties": {"day": {"type": "string", "enum": ["today", "yesterday"]}},
         "additionalProperties": False}, _progress_run))
    r.register(ActionSpec(
        "help", "Explain what the assistant can do. Use for greetings and unclear requests.",
        {"type": "object", "properties": {}, "additionalProperties": False}, _help_run))
    return r


REGISTRY = build_registry()
