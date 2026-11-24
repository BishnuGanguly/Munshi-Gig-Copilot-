"""The English core. One message in, one reply out. Nothing is saved until the user confirms.

Flow: unconfirmed action waiting? -> yes/no/correction/type-answer. Otherwise: delete, correction,
question, help, or a new log (rules first, optional LLM fallback)."""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import dates, parser, replies, tools
from .llm_parser import get_llm_parser
from .replies import fmt_money
from .vocab import HELP_RE

log = logging.getLogger("munshi.engine")


@dataclass
class Outcome:
    reply: str
    entries: list = field(default_factory=list)
    intent: str = "unknown"
    warnings: list = field(default_factory=list)


def _entry_out(e: dict, status: str) -> dict:
    return {**e, "status": status}


def _readback(entries: list[dict], today: date) -> str:
    return "Please confirm: " + "; ".join(replies.describe_entry(e, today) for e in entries) + ". Is that correct?"


def _ask_type(entries: list[dict]) -> str:
    missing = [e for e in entries if e["type"] is None]
    if len(missing) == 1:
        return f"Was {fmt_money(missing[0]['amount'])} income or an expense?"
    return "Were these amounts income or expenses? Please tell me for each one."


def _apply_type(e: dict, etype: str) -> None:
    e["type"] = etype
    if etype == "income":
        e["category"] = "incentive" if e.get("category") == "incentive" else "earnings"
    elif etype == "saving":
        e["category"] = "savings"
    elif e.get("category") in (None, "earnings", "incentive", "savings"):
        e["category"] = "other"


# ------------------------------------------------------------------ queries
def _phrase(label: str, rest: str) -> str:
    return f"{label[0].upper()}{label[1:]} {rest}"


def answer_query(conn, user_id: str, q: parser.Query, today: date) -> str:
    if q.kind == "hours":
        return "I do not track working hours yet."
    if q.kind == "goals":
        return "Savings goals are coming soon."
    label, start, end = dates.resolve_period(q.period, today)
    s = tools.get_summary(conn, user_id, start, end, category=q.category if q.kind == "spending" else None,
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
    # profit / summary
    if s["count"] == 0:
        return f"No entries recorded {label}."
    text = _phrase(label, f"income {fmt_money(s['income'])}, expenses {fmt_money(s['expense'])}, profit {fmt_money(s['profit'])}.")
    text = text.replace(f"{label[0].upper()}{label[1:]} income", f"{label[0].upper()}{label[1:]}: income", 1)
    if q.kind == "summary" and s["saving"] > 0:
        text += f" You also saved {fmt_money(s['saving'])}."
    return text


# ------------------------------------------------------------------ pending actions
def _commit(conn, user_id: str, pending: dict, today: date) -> Outcome:
    kind = pending["kind"]
    tools.clear_pending(conn, user_id)
    if kind == "add":
        for e in pending["entries"]:
            tools.add_transaction(conn, user_id, e["type"], e["amount"], e["category"], e["platform"], e["date"])
        s = tools.get_summary(conn, user_id, today, today)
        return Outcome(f"Saved. Today's profit is {fmt_money(s['profit'])}.",
                       [_entry_out(e, "saved") for e in pending["entries"]], "confirm")
    if kind == "update":
        ok = tools.update_transaction(conn, user_id, pending["tx_id"], **pending["fields"])
        last = tools.get_last_transaction(conn, user_id)
        if not ok or not last:
            return Outcome("I could not find that entry any more.", intent="confirm")
        return Outcome(f"Updated. The last entry is now {replies.describe_tx(last, today)}.", intent="confirm")
    if kind == "delete":
        ok = tools.delete_transaction(conn, user_id, pending["tx_id"])
        return Outcome("Deleted." if ok else "I could not find that entry any more.", intent="confirm")
    return Outcome(replies.NOTHING_PENDING, intent="confirm")


def _correct_pending(conn, user_id: str, pending: dict, c: parser.Correction, now: datetime) -> Outcome:
    today = now.date()
    if pending["kind"] == "delete":
        return Outcome("Say yes to delete the entry, or no to keep it.", intent="correct")
    if pending["kind"] == "update":
        f = pending["fields"]
        for key, val in (("amount", c.new_amount), ("category", c.category), ("platform", c.platform), ("type", c.type)):
            if val is not None:
                f[key] = val
        if f.get("amount") is not None and not (parser.MIN_AMOUNT <= f["amount"] <= parser.MAX_AMOUNT):
            return Outcome(replies.BAD_AMOUNT, intent="correct")
        tools.set_pending(conn, user_id, pending, now)
        last = tools.get_last_transaction(conn, user_id)
        return Outcome(_update_readback(last, f, today), intent="correct")
    entries = pending["entries"]
    if c.old_amount is not None:
        target = next((e for e in entries if e["amount"] == c.old_amount), None)
        if target is None:
            return Outcome(f"I do not see {fmt_money(c.old_amount)} in the entry. " + _readback(entries, today), intent="correct")
    elif len(entries) == 1:
        target = entries[0]
    else:
        return Outcome("Which entry do you want to change? Say for example: 250 not 300.", intent="correct")
    if c.new_amount is not None:
        target["amount"] = c.new_amount
    if c.type:
        _apply_type(target, c.type)
    if c.category and target["type"] == "expense":
        target["category"] = c.category
    if c.platform:
        target["platform"] = c.platform
    if any(parser.validate_entry(e) for e in entries):
        return Outcome(replies.BAD_AMOUNT, intent="correct")
    tools.set_pending(conn, user_id, pending, now)
    return Outcome(_readback(entries, today), [_entry_out(e, "pending_confirmation") for e in entries], "correct")


def _update_readback(last: dict, fields: dict, today: date) -> str:
    new = {**last, **{("occurred_on" if k == "date" else k): v for k, v in fields.items()}}
    return (f"Change the last entry from {replies.describe_tx(last, today)} to {replies.describe_tx(new, today)}? "
            "Say yes to confirm.")


# ------------------------------------------------------------------ main entry
def handle(conn, user_id: str, text: str, now: datetime | None = None, llm=None) -> Outcome:
    now = now or dates.now_ist()
    today = now.date()
    text = text.strip()
    pending = tools.get_pending(conn, user_id, now)
    note = ""

    if pending:
        if pending.get("awaiting") == "type" and (tcue := parser.find_type_cue(text)) and not parser.is_no(text):
            for e in pending["entries"]:
                if e["type"] is None:
                    _apply_type(e, tcue)
            if any(parser.validate_entry(e) for e in pending["entries"]):
                return Outcome(_ask_type(pending["entries"]), intent="clarify")
            pending.pop("awaiting")
            tools.set_pending(conn, user_id, pending, now)
            return Outcome(_readback(pending["entries"], today),
                           [_entry_out(e, "pending_confirmation") for e in pending["entries"]], "clarify")
        if parser.is_yes(text):
            if pending.get("awaiting") == "type":
                return Outcome(_ask_type(pending["entries"]), intent="clarify")
            return _commit(conn, user_id, pending, today)
        if parser.is_no(text) or (parser.is_delete(text) and pending["kind"] == "add"):
            tools.clear_pending(conn, user_id)
            return Outcome("OK, discarded. Please say it again.", intent="reject")
        corr = parser.parse_correction(text)
        if corr and pending.get("awaiting") != "type":
            return _correct_pending(conn, user_id, pending, corr, now)
        # a different message: questions and help leave the pending action alone, anything else replaces it
        q = parser.parse_query(text)
        if q and parser.find_amount(text) is None:
            return Outcome(answer_query(conn, user_id, q, today) + " Your earlier entry is still waiting for confirmation.",
                           intent="query")
        if HELP_RE.match(text):
            return Outcome(replies.HELP + " Your earlier entry is still waiting for confirmation.", intent="help")
        if parser.find_amount(text) is not None:
            note = "Your earlier unsaved entry was dropped. "
            tools.clear_pending(conn, user_id)
        else:
            return Outcome("Please say yes to confirm or no to cancel.", intent="clarify")

    # ---- no (usable) pending action
    if parser.is_yes(text) or parser.is_no(text):
        return Outcome(replies.NOTHING_PENDING, intent="confirm")

    if parser.is_delete(text):
        last = tools.get_last_transaction(conn, user_id)
        if not last:
            return Outcome(replies.NO_ENTRIES, intent="delete")
        tools.set_pending(conn, user_id, {"kind": "delete", "tx_id": last["id"]}, now)
        return Outcome(f"Delete the last entry ({replies.describe_tx(last, today)})? Say yes to confirm.", intent="delete")

    corr = parser.parse_correction(text)
    if corr:
        last = tools.get_last_transaction(conn, user_id)
        if not last:
            return Outcome("There is no saved entry to correct.", intent="correct")
        fields = {"amount": corr.new_amount, "category": corr.category, "platform": corr.platform, "type": corr.type}
        fields = {k: v for k, v in fields.items() if v is not None}
        if "amount" in fields and not (parser.MIN_AMOUNT <= fields["amount"] <= parser.MAX_AMOUNT):
            return Outcome(replies.BAD_AMOUNT, intent="correct")
        tools.set_pending(conn, user_id, {"kind": "update", "tx_id": last["id"], "fields": fields}, now)
        return Outcome(_update_readback(last, fields, today), intent="correct")

    q = parser.parse_query(text)
    if q and (parser.find_amount(text) is None or re.match(r"\s*(how|what|which|show|tell)\b", text, re.I)):
        return Outcome(answer_query(conn, user_id, q, today), intent="query")

    if HELP_RE.match(text):
        return Outcome(replies.HELP, intent="help")

    result = parser.parse_log(text, today)
    warnings: list[str] = []
    if not result.entries and re.search(r"\d", text):
        llm = llm or get_llm_parser()
        if llm is not None:
            entries = llm.parse(text, today)
            if entries:
                result = parser.LogParse(entries)
                warnings.append("llm_parser_used")
    if not result.entries:
        return Outcome(note + replies.NOT_UNDERSTOOD, intent="unknown", warnings=warnings)
    if "amount_out_of_range" in result.problems:
        return Outcome(note + replies.BAD_AMOUNT, intent="log", warnings=warnings)
    entries = result.entries
    if "needs_type" in result.problems:
        tools.set_pending(conn, user_id, {"kind": "add", "entries": entries, "awaiting": "type"}, now)
        return Outcome(note + _ask_type(entries), [_entry_out(e, "needs_type") for e in entries], "log", warnings)
    tools.set_pending(conn, user_id, {"kind": "add", "entries": entries}, now)
    return Outcome(note + _readback(entries, today), [_entry_out(e, "pending_confirmation") for e in entries], "log", warnings)
