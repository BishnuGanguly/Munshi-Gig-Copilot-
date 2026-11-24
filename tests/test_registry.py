import json
from datetime import datetime

import pytest

from app.core import db, dates, tools
from app.core.actions import REGISTRY, build_registry
from app.core.registry import ActionError, ActionSpec, Context, Outcome, Registry, validate_args

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)


@pytest.fixture
def ctx():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    return Context(conn, "u", NOW)


SCHEMA = {"type": "object", "properties": {
    "n": {"type": "number", "minimum": 1, "maximum": 10}, "s": {"type": "string", "enum": ["a", "b"]},
    "maybe": {"type": ["string", "null"]}, "items": {"type": "array", "minItems": 1, "items": {"type": "integer"}}},
    "required": ["n"], "additionalProperties": False}


@pytest.mark.parametrize("args,ok", [
    ({"n": 5}, True), ({"n": 5, "s": "a", "maybe": None, "items": [1, 2]}, True),
    ({}, False), ({"n": 0}, False), ({"n": 11}, False), ({"n": "5"}, False), ({"n": True}, False),
    ({"n": 5, "s": "c"}, False), ({"n": 5, "extra": 1}, False), ({"n": 5, "items": []}, False),
    ({"n": 5, "items": [1.5]}, False), ({"n": 5, "maybe": 3}, False), ("not a dict", False),
])
def test_validate_args(args, ok):
    assert (validate_args(SCHEMA, args) == []) == ok


def test_tool_definitions_are_valid_llm_tools():
    defs = REGISTRY.tool_definitions()
    assert {d["name"] for d in defs} == {"log_entries", "correct_entry", "delete_entry", "query_summary", "set_target",
                                         "get_target_progress", "help"}
    for d in defs:
        assert d["description"] and d["input_schema"]["type"] == "object"
        json.dumps(d)                                  # must be serialisable to send to a model


def test_user_identity_is_never_an_argument():
    """A model or client must not be able to act as another user."""
    for d in REGISTRY.tool_definitions():
        assert "user_id" not in d["input_schema"].get("properties", {})


def test_write_actions_only_propose(ctx):
    out = REGISTRY.execute("log_entries", {"entries": [{"type": "income", "amount": 500, "platform": "Swiggy"}]}, ctx)
    assert "Please confirm" in out.reply
    assert ctx.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert out.actions[0]["name"] == "log_entries" and out.actions[0]["stage"] == "proposed"
    committed = REGISTRY.commit(ctx, tools.get_pending(ctx.conn, "u", NOW))
    assert committed.reply.startswith("Saved") and committed.actions[0]["stage"] == "committed"
    assert tools.get_pending(ctx.conn, "u", NOW) is None
    assert ctx.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_execute_rejects_bad_calls(ctx):
    with pytest.raises(ActionError, match="unknown action"):
        REGISTRY.execute("drop_tables", {}, ctx)
    with pytest.raises(ActionError, match="missing 'amount'"):
        REGISTRY.execute("set_target", {}, ctx)
    assert "between ₹100 and ₹50,000" in REGISTRY.execute("set_target", {"amount": 50}, ctx).reply   # handler, not schema
    with pytest.raises(ActionError):
        REGISTRY.execute("set_target", {"amount": "lots"}, ctx)
    with pytest.raises(ActionError, match="unknown field"):
        REGISTRY.execute("help", {"user_id": "someone-else"}, ctx)
    with pytest.raises(ActionError):
        REGISTRY.execute("log_entries", {"entries": [{"amount": 5, "type": "gift"}]}, ctx)


def test_read_only_actions_run_directly(ctx):
    out = REGISTRY.execute("query_summary", {"kind": "earnings", "period": "today"}, ctx)
    assert out.reply == "No income recorded today." and out.actions[0]["stage"] == "run"


def test_registration_rules():
    r = Registry()
    spec = ActionSpec("x", "d", {"type": "object"}, lambda c, a: Outcome("ok"))
    r.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        r.register(spec)
    with pytest.raises(ValueError, match="needs a commit"):
        r.register(ActionSpec("w", "d", {"type": "object"}, lambda c, a: Outcome("ok"), read_only=False))
    assert build_registry().names() == REGISTRY.names()


def test_new_actions_plug_in_without_touching_the_router(ctx):
    """The point of the registry: a future feature is one registration."""
    r = build_registry()
    r.register(ActionSpec("ping", "Reply pong.", {"type": "object", "properties": {}}, lambda c, a: Outcome("pong")))
    assert r.execute("ping", {}, ctx).reply == "pong" and "ping" in [d["name"] for d in r.tool_definitions()]
