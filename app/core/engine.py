"""The router. One message in, one reply out. Nothing is saved until the user confirms.

Routing order: an action waiting for confirmation (yes / no / correction / answer) -> target setting and questions
-> delete -> correction -> questions -> help -> a new log (rules first, optional LLM fallback).
Every capability itself lives in actions.py and is reached through the registry, so an LLM tool-calling layer
can later replace this routing without touching the handlers."""
import logging
import re
from datetime import datetime

from . import actions, dates, db, parser, replies, tools
from .actions import REGISTRY, answer_query, ask_type, entry_out, readback, apply_type  # noqa: F401  (re-exported)
from .llm_parser import get_llm_parser
from .llm_router import build_context, get_llm_router
from .registry import Context, Outcome
from .vocab import HELP_RE

log = logging.getLogger("munshi.engine")
STILL_WAITING = " Your earlier entry is still waiting for confirmation."


def _read_only(ctx: Context, text: str, strict: bool) -> Outcome | None:
    """Target questions, general questions and help. `strict` (used while something awaits confirmation) answers a
    question only when the message holds no amount, so 'spent 250' is never mistaken for a question."""
    day = parser.parse_target_query(text)
    if day:
        return REGISTRY.execute("get_target_progress", {"day": day}, ctx)
    q = parser.parse_query(text)
    if q and (parser.find_amount(text) is None or (not strict and re.match(r"\s*(how|what|which|show|tell)\b", text, re.I))):
        args = {"kind": q.kind, "period": q.period, "category": q.category, "platform": q.platform}
        return REGISTRY.execute("query_summary", args, ctx)
    if HELP_RE.match(text):
        return REGISTRY.execute("help", {}, ctx)
    return None


def _handle_pending(ctx: Context, pending: dict, text: str) -> Outcome | str:
    """Returns an Outcome when the message was dealt with, or a note (pending already cleared) to carry on routing."""
    conn, user_id, now, today = ctx.conn, ctx.user_id, ctx.now, ctx.today
    action = pending["action"]
    awaiting_type = action == "log_entries" and pending.get("awaiting") == "type"

    if awaiting_type and (tcue := parser.find_type_cue(text)) and not parser.is_no(text):
        for e in pending["entries"]:
            if e["type"] is None:
                apply_type(e, tcue)
        if any(parser.validate_entry(e) for e in pending["entries"]):
            return Outcome(ask_type(pending["entries"]), intent="clarify")
        pending.pop("awaiting")
        tools.set_pending(conn, user_id, pending, now)
        return Outcome(readback(pending["entries"], today),
                       [entry_out(e, "pending_confirmation") for e in pending["entries"]], "clarify")
    if parser.is_yes(text):
        if awaiting_type:
            return Outcome(ask_type(pending["entries"]), intent="clarify")
        return REGISTRY.commit(ctx, pending)
    if parser.is_no(text) or (parser.is_delete(text) and action == "log_entries"):
        tools.clear_pending(conn, user_id)
        return Outcome("OK, discarded. Please say it again.", intent="reject")
    corr = parser.parse_correction(text)
    if corr and not awaiting_type:
        return actions.correct_pending(ctx, pending, corr)
    # a different message: questions and help leave the pending action alone, anything with an amount replaces it
    out = _read_only(ctx, text, strict=True)
    if out:
        out.reply += STILL_WAITING
        return out
    if parser.find_amount(text) is not None:
        tools.clear_pending(conn, user_id)
        return "Your earlier unsaved entry was dropped. "
    return Outcome("Please say yes to confirm or no to cancel.", intent="clarify")


def handle(conn, user_id: str, text: str, now: datetime | None = None, llm=None, router=None) -> Outcome:
    """`llm` is the legacy extraction fallback, `router` the LLM understanding layer. Both are optional and
    are only reached when the rules cannot route the message."""
    ctx = Context(conn, user_id, now or dates.now_ist())
    text = text.strip()
    note = ""

    pending = tools.get_pending(conn, user_id, ctx.now)
    if pending:
        out = _handle_pending(ctx, pending, text)
        if isinstance(out, Outcome):
            return out
        note = out

    if parser.is_yes(text) or parser.is_no(text):
        return Outcome(replies.NOTHING_PENDING, intent="confirm")

    amount = parser.parse_target_set(text)
    if amount is not None:
        out = REGISTRY.execute("set_target", {"amount": amount}, ctx)
        out.reply = note + out.reply
        return out

    if parser.is_delete(text):
        return REGISTRY.execute("delete_entry", {}, ctx)

    corr = parser.parse_correction(text)
    if corr:
        args = {k: v for k, v in (("amount", corr.new_amount), ("category", corr.category),
                                  ("platform", corr.platform), ("type", corr.type)) if v is not None}
        return REGISTRY.execute("correct_entry", args, ctx)

    out = _read_only(ctx, text, strict=False)
    if out:
        return out

    result = parser.parse_log(text, ctx.today)
    warnings: list[str] = []
    # Ask the LLM understanding layer when the rules cannot place the message at all, or found an amount
    # but could not tell income from expense (exactly where a model reads the sentence better than regexes).
    if not result.entries or "needs_type" in result.problems:
        router = router or get_llm_router(REGISTRY)
        if router is not None:
            out = router.route(ctx, text, build_context(ctx, None, db.recent_turns(conn, user_id),
                                                        tools.get_target(conn, user_id, ctx.today)))
            if out is not None:
                out.reply = note + out.reply
                return out
        if not result.entries and re.search(r"\d", text):
            llm = llm or get_llm_parser()
            if llm is not None:
                entries = llm.parse(text, ctx.today)
                if entries:
                    result = parser.LogParse(entries)
                    warnings.append("llm_parser_used")
    if not result.entries:
        return Outcome(note + replies.NOT_UNDERSTOOD, intent="unknown", warnings=warnings)
    out = REGISTRY.execute("log_entries", {"entries": result.entries}, ctx)
    out.reply = note + out.reply
    out.warnings += warnings
    return out
