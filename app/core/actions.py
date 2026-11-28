"""Action handlers, and the registry that exposes them. Logic only; app/core/brain.py routes to them.

Every number shown to the user is computed here with tools.* (SQL/code), never by a model."""
from datetime import date

from . import analysis, dates, insights, replies, tools, validate
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
    """Fill defaults so entries from any source have the same shape, and make the category fit the type."""
    e = dict(e)
    e.setdefault("type", None)
    e.setdefault("platform", None)
    if not e.get("date"):
        e["date"] = today.isoformat()
    e["category"] = validate.fit_category(e["type"], e.get("category"))
    return e


def _same_entry(old: dict, new: dict) -> bool:
    """`new` restates `old`: the same amount, and a type that agrees (or answers the one that was missing)."""
    return old.get("amount") == new.get("amount") and old.get("type") in (None, new.get("type"))


def merge_waiting(waiting: list[dict], new: list[dict], replace: bool) -> list[dict]:
    """Entries the rider has not confirmed yet, plus the ones just reported.

    A rider often adds to a read-back before saying yes ("uber se 300" ... "aur chai 20"). Replacing the
    waiting entries then would silently lose the first one, so new entries are ADDED. An entry that restates
    a waiting one (the same amount, e.g. the answer to "income or expense?") takes its place. Only an explicit
    correction (`replace_waiting`) discards what was waiting."""
    if replace:
        return new
    kept = [w for w in waiting if not any(_same_entry(w, n) for n in new)]
    return kept + new


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
    new = [_complete(e, ctx.today) for e in args["entries"]]
    problems = [p for e in new for p in validate.validate_entry(e, ctx.today)]
    if "amount_out_of_range" in problems:
        return Outcome(replies.BAD_AMOUNT, intent="log")
    if "bad_date" in problems:
        return Outcome(replies.BAD_DATE, intent="log")
    waiting = tools.get_pending(ctx.conn, ctx.user_id, ctx.now)
    if waiting and waiting.get("action") == "log_entries":
        entries = merge_waiting(waiting["entries"], new, bool(args.get("replace_waiting")))
    else:
        entries = new
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
    # The fixed sentence is what the rider hears without a model, and whenever the model's choice is unusable.
    reply = "Saved. " + insights.after_save(ctx.conn, ctx.user_id, today, pending["entries"], before)
    facts = insights.facts_after_save(ctx.conn, ctx.user_id, today, pending["entries"], before)
    return Outcome(reply, [entry_out(e, "saved") for e in pending["entries"]], "confirm", facts=facts)


# ------------------------------------------------------------------ correct_entry / delete_entry (the last SAVED entry)
def _correct_run(ctx: Context, args: dict) -> Outcome:
    last = tools.get_last_transaction(ctx.conn, ctx.user_id)
    if not last:
        return Outcome("There is no saved entry to correct.", intent="correct")
    fields = {k: args[k] for k in ("amount", "category", "platform", "type") if args.get(k) is not None}
    if not fields:
        return Outcome("What should I change? For example: it should be 300.", intent="correct")
    if "type" in fields or "category" in fields:     # an income left as "fuel" would be nonsense
        fitted = validate.fit_category(fields.get("type", last["type"]), fields.get("category", last["category"]))
        if fitted != last["category"] or "category" in fields:
            fields["category"] = fitted
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


def _top(d: dict, n: int = 3) -> str:
    return ", ".join(f"{k} {fmt_money(v)}" for k, v in list(d.items())[:n])


def _kinds(d: dict) -> str:
    """A breakdown in brackets: just the name when there is one (the total already says the amount)."""
    return next(iter(d)) if len(d) == 1 else _top(d)


def _income_part(s: dict) -> str:
    text = f"income {fmt_money(s['income'])}"
    return text + f" ({_top(s['income_by_platform'])})" if len(s["income_by_platform"]) >= 2 else text


def _expense_part(s: dict) -> str:
    if s["expense"] == 0:
        return "no expenses"
    text = f"expenses {fmt_money(s['expense'])}"
    return text + f" ({_top(s['expense_by_category'])})" if s["expense_by_category"] else text


def daily_summary(ctx: Context, day: date, label: str) -> str:
    """The end-of-day report: where the money came from, where it went, the profit, the target, and how the
    day compares with the rider's recent working days. Every figure comes from SQL."""
    s = tools.get_summary(ctx.conn, ctx.user_id, day, day)
    if s["count"] == 0:
        return f"No entries recorded {label}."
    parts = [_phrase(label, f": {_income_part(s)}").replace(" :", ":"),
             _expense_part(s)[0].upper() + _expense_part(s)[1:], f"Profit {fmt_money(s['profit'])}"]
    if s["saving"] > 0:
        parts.append(f"You also saved {fmt_money(s['saving'])}")
    p = tools.target_progress(ctx.conn, ctx.user_id, day)
    if p:
        parts.append(f"Target {fmt_money(p['target'])} reached" if p["met"]
                     else f"{fmt_money(p['remaining'])} short of your {fmt_money(p['target'])} target")
    average, worked = tools.recent_daily_income(ctx.conn, ctx.user_id, day)
    if worked >= 3 and s["income"] > 0:
        gap = round(s["income"] - average, 2)
        if abs(gap) < 1:
            parts.append(f"About the same as your recent daily average of {fmt_money(average)}")
        else:
            parts.append(f"Income is {fmt_money(abs(gap))} {'above' if gap > 0 else 'below'} your recent "
                         f"daily average of {fmt_money(average)}")
    return ". ".join(parts) + "."


def _costs_part(p: dict) -> str:
    return f"costs {fmt_money(p['costs'])} ({_kinds(p['costs_by_category'])})"


def _shared_note(shared: dict) -> str:
    if shared["total"] <= 0:
        return ""
    return (f" Shared costs of {fmt_money(shared['total'])} ({_kinds(shared['by_category'])}) "
            "are not split between platforms.")


def platform_profit(ctx: Context, platform: str, label: str, start, end) -> str:
    b = tools.platform_breakdown(ctx.conn, ctx.user_id, start, end)
    p = b["platforms"].get(platform)
    if not p:
        return f"Nothing recorded for {platform} {label}."
    text = _phrase(label, f"from {platform}: earned {fmt_money(p['income'])}")
    if p["costs"] > 0:
        text += f", {_costs_part(p)}, net {fmt_money(p['net'])}"
    return text + "." + _shared_note(b["shared"])


def platform_report(ctx: Context, label: str, start, end, period: str) -> str:
    """Every platform's own profit, side by side. Over several days each platform is also given per working
    day, which is a fairer comparison than totals: a rider may simply have spent more days on one app."""
    b = tools.platform_breakdown(ctx.conn, ctx.user_id, start, end)
    if not b["platforms"]:
        return f"No income with a platform recorded {label}."
    single_day = period in ("today", "yesterday")
    items = []
    for name, p in b["platforms"].items():
        item = f"{name} net {fmt_money(p['net'])}"
        if p["costs"] > 0:
            item += f" (earned {fmt_money(p['income'])}, {_costs_part(p)})"
        if not single_day and p["days"] > 1:
            item += f", {fmt_money(round(p['net'] / p['days']))} a day over {p['days']} days"
        items.append(item)
    return _phrase(label, "by platform: ") + "; ".join(items) + "." + _shared_note(b["shared"])


def answer_query(ctx: Context, q: validate.Query) -> str:
    if q.kind == "hours":
        return "I do not track working hours yet."
    label, start, end = dates.resolve_period(q.period, ctx.today)
    if q.kind == "platform_breakdown":
        return platform_report(ctx, label, start, end, q.period)
    if q.kind in ("profit", "summary") and q.platform:
        return platform_profit(ctx, q.platform, label, start, end)
    if q.kind == "summary" and q.period in ("today", "yesterday"):
        return daily_summary(ctx, start, label)
    # Every filter the rider asked for is applied; one we could not apply would give a confidently wrong total.
    s = tools.get_summary(ctx.conn, ctx.user_id, start, end, category=q.category, platform=q.platform)
    if q.kind == "earnings":
        source = f" from {q.platform}" if q.platform else ""
        what = " in incentives" if q.category == "incentive" else ""
        if s["income"] == 0:
            return f"No income{source}{what} recorded {label}."
        text = _phrase(label, f"you earned {fmt_money(s['income'])}{source}{what}")
        if len(s["income_by_platform"]) >= 2 and not q.platform:
            text += ". " + _top(s["income_by_platform"])
        return text + "."
    if q.kind == "spending":
        on = f" on {q.category}" if q.category else ""
        on += f" for {q.platform}" if q.platform else ""
        if s["expense"] == 0:
            return f"No expenses{on} recorded {label}."
        text = _phrase(label, f"you spent {fmt_money(s['expense'])}{on}")
        if not q.category and s["expense_by_category"]:
            cat, val = next(iter(s["expense_by_category"].items()))
            text += f". Biggest: {cat} {fmt_money(val)}"
        return text + "."
    if q.kind == "savings":
        return f"No savings recorded {label}." if s["saving"] == 0 else _phrase(label, f"you saved {fmt_money(s['saving'])}.")
    if s["count"] == 0:                                              # profit / summary over a longer period
        return f"No entries recorded {label}."
    head = f"{label[0].upper()}{label[1:]}"
    if q.kind == "summary":
        text = f"{head}: {_income_part(s)}. {_expense_part(s)[0].upper()}{_expense_part(s)[1:]}. Profit {fmt_money(s['profit'])}."
        return text + (f" You also saved {fmt_money(s['saving'])}." if s["saving"] > 0 else "")
    return f"{head}: income {fmt_money(s['income'])}, expenses {fmt_money(s['expense'])}, profit {fmt_money(s['profit'])}."


def _query_run(ctx: Context, args: dict) -> Outcome:
    q = validate.Query(args["kind"], args.get("period") or "today", args.get("category"), args.get("platform"))
    return Outcome(answer_query(ctx, q), intent="query")


# ------------------------------------------------------------------ analyze
# A month of data is what a weekday pattern needs; "this week" would rarely have each day twice.
ANALYZE_DEFAULT_PERIOD = "last_30_days"


def _analyze_run(ctx: Context, args: dict) -> Outcome:
    group_by = args["group_by"]
    measure = args.get("measure") or ("costs" if group_by == "category" else "income")
    label, start, end = dates.resolve_period(args.get("period") or ANALYZE_DEFAULT_PERIOD, ctx.today)
    result = analysis.analyze(ctx.conn, ctx.user_id, group_by, measure, start, end, today=ctx.today)
    reply = analysis.summary_sentence(result, group_by, label)
    if len(result["rows"]) < analysis.MIN_GROUPS:
        return Outcome(reply, intent="analyze")          # nothing to explain: say so plainly
    return Outcome(reply, intent="analyze", facts=analysis.facts(result, group_by, label), facts_for="analysis")


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
        "platform": {"type": ["string", "null"], "enum": PLATFORMS + [None],
                     "description": "For income, the app it came from. For an expense, only when the cost belongs "
                                    "to one app (a penalty, commission or app fee); leave it out for shared costs "
                                    "like fuel"},
        "date": {"type": "string", "description": "ISO date YYYY-MM-DD; today unless the user said otherwise"},
    },
    "required": ["amount"],
    "additionalProperties": False,
}
_QUERY_KINDS = ["earnings", "spending", "profit", "platform_breakdown", "summary", "savings", "hours"]
_PERIODS = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "last_30_days", "all"]


def build_registry() -> Registry:
    r = Registry()
    r.register(ActionSpec(
        "log_entries", "Record income, expenses or savings the user just reported. Needs the user's confirmation before saving.",
        {"type": "object", "properties": {
            "entries": {"type": "array", "minItems": 1, "maxItems": 10, "items": _ENTRY},
            "replace_waiting": {"type": "boolean", "description": "true only when the rider is CORRECTING entries "
                                "still waiting for confirmation; then send the full corrected list. Leave it out "
                                "when the rider adds something new: it is added to what is waiting"}},
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
        "query_summary", "Answer a question about the user's own money for a period (which app or day is BEST is `analyze`). kind=summary is the full report (use it for "
        "'aaj ka hisaab', 'how was my day'); platform_breakdown compares profit per app; profit or summary with a "
        "platform is that one app's own profit.",
        {"type": "object", "properties": {
            "kind": {"type": "string", "enum": _QUERY_KINDS}, "period": {"type": "string", "enum": _PERIODS},
            "category": {"type": ["string", "null"], "enum": CATEGORIES + [None]},
            "platform": {"type": ["string", "null"], "enum": PLATFORMS + [None]}},
         "required": ["kind"], "additionalProperties": False}, _query_run))
    r.register(ActionSpec(
        "analyze", "Find a pattern in the user's own money: which weekday, platform, cost or week is highest or "
        "lowest. Use for questions like 'which day is best for me', 'which app pays most after costs', 'where does "
        "my money go', 'which week was worst'. Not for a plain total (that is query_summary).",
        {"type": "object", "properties": {
            "group_by": {"type": "string", "enum": analysis.GROUPS,
                         "description": "weekday: which day of the week; platform: which app; category: which "
                                        "kind of cost; week or day: which week or date"},
            "measure": {"type": "string", "enum": analysis.MEASURES,
                        "description": "what to compare; leave out for income (costs for category)"},
            "period": {"type": "string", "enum": _PERIODS, "description": "leave out for the last 30 days"}},
         "required": ["group_by"], "additionalProperties": False}, _analyze_run))
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
