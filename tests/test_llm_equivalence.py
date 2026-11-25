"""The point of the provider layer: the same rider message, answered through different wire formats, gives the
same result. Each scenario is replayed through the Anthropic adapter and the OpenAI-style adapter, with each
vendor's own response shape, and everything downstream (validation, guards, the database) must agree."""
import json
from datetime import datetime

import httpx
import pytest

from app.core import brain, dates, db
from app.core.actions import REGISTRY
from app.core.brain import Understander
from app.llm import FallbackLlm
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.openai_compat import OpenAICompatProvider
from tests.test_llm_anthropic import Block, FakeMessages
from tests.fakes import FakeProvider

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)


# ---------------------------------------------------------------- one scenario, two vendors
def via_anthropic(name, args):
    msgs = FakeMessages(content=[Block("tool_use", name=name, input=args)], stop_reason="tool_use")
    return AnthropicProvider(client=type("C", (), {"messages": msgs})())


def via_openai_style(name, args):
    body = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}
    return OpenAICompatProvider("sarvam", "https://api.sarvam.ai/v1", "sarvam-105b", api_key="k",
                                auth="header:api-subscription-key",
                                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))


VENDORS = [pytest.param(via_anthropic, id="anthropic"), pytest.param(via_openai_style, id="openai-style")]


def run(vendor, message, name, args, confirm=False):
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    u = Understander(REGISTRY, vendor(name, args))
    out = brain.handle(conn, "u", message, NOW, understander=u, renderer=None)
    if confirm:
        out = brain.handle(conn, "u", "haan", NOW, understander=u, renderer=None)
    rows = [dict(r) for r in conn.execute("SELECT type, amount, category, platform FROM transactions")]
    return out, rows


@pytest.mark.parametrize("vendor", VENDORS)
def test_logging_two_entries(vendor):
    out, rows = run(vendor, "Swiggy se 1400 aaye aur petrol 250 ka dala", "log_entries", {"entries": [
        {"type": "income", "amount": 1400, "platform": "Swiggy"},
        {"type": "expense", "amount": 250, "category": "fuel"}]}, confirm=True)
    assert out.outcome.reply == "Saved. Today's profit is ₹1,150."
    assert rows == [{"type": "income", "amount": 1400, "category": "earnings", "platform": "Swiggy"},
                    {"type": "expense", "amount": 250, "category": "fuel", "platform": None}]


@pytest.mark.parametrize("vendor", VENDORS)
def test_a_question_and_a_no_argument_action(vendor):
    out, _ = run(vendor, "is hafte kitna kamaya", "query_summary", {"kind": "earnings", "period": "this_week"})
    assert out.outcome.reply == "No income recorded this week."
    out, _ = run(vendor, "kuch samajh nahi aaya", "help", {})
    assert out.outcome.intent == "help"


@pytest.mark.parametrize("vendor", VENDORS)
def test_the_same_guards_apply_whoever_answered(vendor):
    """An invented amount, an unknown action and a smuggled user_id are refused identically."""
    for message, name, args in [
        ("Swiggy ka paisa aaya", "log_entries", {"entries": [{"type": "income", "amount": 9999}]}),
        ("kuch bhi", "drop_tables", {}),
        ("victim ka paisa", "query_summary", {"kind": "earnings", "user_id": "victim"}),
    ]:
        out, rows = run(vendor, message, name, args)
        assert out.outcome.intent == "unknown" and rows == []


# ---------------------------------------------------------------- an outage, end to end
def test_sarvam_outage_falls_back_to_claude_and_the_rider_never_notices(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    sarvam = OpenAICompatProvider("sarvam", "https://api.sarvam.ai/v1", "sarvam-105b", api_key="k", retries=1,
                                  auth="header:api-subscription-key",
                                  transport=httpx.MockTransport(lambda r: httpx.Response(503, text="busy")))
    claude = via_anthropic("log_entries", {"entries": [{"type": "income", "amount": 800, "platform": "Zomato"}]})
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    u = Understander(REGISTRY, FallbackLlm([sarvam, claude]))
    out = brain.handle(conn, "u", "aaj zomato se 800 aaye", NOW, understander=u, renderer=None)
    assert out.outcome.reply == "Please confirm: income ₹800 from Zomato. Is that correct?"


def test_everything_down_is_an_honest_unknown_not_an_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    dead = lambda: OpenAICompatProvider("p", "http://h/v1", "m", api_key="k", retries=0,
                                        transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    u = Understander(REGISTRY, FallbackLlm([dead(), dead()]))
    out = brain.handle(conn, "u", "aaj zomato se 800 aaye", NOW, understander=u, renderer=None)
    assert out.outcome.intent == "unknown" and "could not understand" in out.outcome.reply


def test_the_renderer_degrades_to_english_when_its_provider_is_down():
    dead = OpenAICompatProvider("p", "http://h/v1", "m", api_key="k", retries=0,
                                transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    reply, lang, warnings = brain.Renderer(dead).render("Saved. Today's profit is ₹350.", "kitna hua")
    assert (reply, lang, warnings) == ("Saved. Today's profit is ₹350.", "en-IN", ["reply_not_rendered"])


def test_rendering_through_the_openai_style_adapter_strips_reasoning():
    """A model that thinks aloud must not leak its reasoning into a rider's reply."""
    content = '<think>The rider wrote Hinglish, so I reply in Hinglish.</think>{"language":"hi-Latn","reply":"Save ho gaya. Aaj ka munafa ₹350 hai."}'
    p = OpenAICompatProvider("p", "http://h/v1", "m", api_key="k",
                             transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
                                 "choices": [{"message": {"content": content}}]})))
    reply, lang, warnings = brain.Renderer(p).render("Saved. Today's profit is ₹350.", "kitna hua")
    assert reply == "Save ho gaya. Aaj ka munafa ₹350 hai." and lang == "hi-Latn" and warnings == []
