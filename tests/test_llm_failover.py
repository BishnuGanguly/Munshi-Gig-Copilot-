"""An outage, end to end: the first provider is down, the second answers, and the rider never notices.

Both providers speak the same OpenAI-style protocol (that is the only wire format left), each against its own
mocked server, so the whole path - adapter, chain, brain, action, database - is exercised.
"""
import json
from datetime import datetime

import httpx

from app.core import brain, dates, db
from app.core.actions import REGISTRY
from app.core.brain import Understander
from app.llm import FallbackLlm
from app.llm.openai_compat import OpenAICompatProvider

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)


def server(name, handler, **kw):
    return OpenAICompatProvider(name, f"https://{name}.example.com/v1", "m", api_key="k", retries=kw.pop("retries", 0),
                                transport=httpx.MockTransport(handler), **kw)


def answers(name, args):
    body = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}]}
    return lambda r: httpx.Response(200, json=body)


def conversation(provider):
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    return conn, Understander(REGISTRY, provider)


def test_the_first_provider_down_the_second_answers(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    down = server("first", lambda r: httpx.Response(503, text="busy"), retries=1)
    up = server("second", answers("log_entries", {"entries": [{"type": "income", "amount": 800, "platform": "Zomato"}]}))
    conn, u = conversation(FallbackLlm([down, up]))
    out = brain.handle(conn, "u", "aaj zomato se 800 aaye", NOW, understander=u, renderer=None)
    assert out.outcome.reply == "Please confirm: income ₹800 from Zomato. Is that correct?"


def test_everything_down_is_an_honest_unknown_not_an_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    conn, u = conversation(FallbackLlm([server("a", lambda r: httpx.Response(500)),
                                        server("b", lambda r: httpx.Response(500))]))
    out = brain.handle(conn, "u", "aaj zomato se 800 aaye", NOW, understander=u, renderer=None)
    assert out.outcome.intent == "unknown" and "could not understand" in out.outcome.reply


def test_the_same_guards_apply_to_whichever_provider_answered():
    """An invented amount, an unknown action and a smuggled user_id are refused whoever replied."""
    for message, name, args in [
        ("Swiggy ka paisa aaya", "log_entries", {"entries": [{"type": "income", "amount": 9999}]}),
        ("kuch bhi", "drop_tables", {}),
        ("victim ka paisa", "query_summary", {"kind": "earnings", "user_id": "victim"}),
    ]:
        conn, u = conversation(server("p", answers(name, args)))
        out = brain.handle(conn, "u", message, NOW, understander=u, renderer=None)
        assert out.outcome.intent == "unknown"
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_the_renderer_degrades_to_english_when_its_provider_is_down():
    dead = server("p", lambda r: httpx.Response(500))
    reply, lang, warnings = brain.Renderer(dead).render("Saved. Today's profit is ₹350.", "kitna hua")
    assert (reply, lang, warnings) == ("Saved. Today's profit is ₹350.", "en-IN", ["reply_not_rendered"])


def test_rendering_strips_a_models_reasoning():
    """A model that thinks aloud must not leak its reasoning into a rider's reply."""
    content = ('<think>The rider wrote Hinglish, so I reply in Hinglish.</think>'
               '{"language":"hi-Latn","reply":"Save ho gaya. Aaj ka munafa ₹350 hai."}')
    p = server("p", lambda r: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    reply, lang, warnings = brain.Renderer(p).render("Saved. Today's profit is ₹350.", "kitna hua")
    assert reply == "Save ho gaya. Aaj ka munafa ₹350 hai." and lang == "hi-Latn" and warnings == []
