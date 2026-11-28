"""The model chooses the insight after a save; code computes every fact it may choose from.

Three things are tested: the fact sheet is right (and leaves out what would mislead), the model's answer is
only used when every number in it is a computed fact, and anything else falls back to a correct reply.
"""
import json
from datetime import datetime, timedelta

import pytest

from app.core import brain, dates, db, tools
from app.core.actions import REGISTRY
from app.core.brain import SYSTEM_INSIGHT, SYSTEM_RENDER, Renderer
from app.core.insights import facts_after_save
from app.core.registry import Context
from tests.fakes import FakeProvider, rendering

NOW = datetime(2026, 11, 25, 21, 0, tzinfo=dates.IST)   # a Wednesday; the week began on Monday the 23rd
TODAY = NOW.date()


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def app():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")

    def save(*entries):
        """Log and confirm; returns the save's Outcome (fixed reply + facts)."""
        ctx = Context(conn, "u", NOW)
        REGISTRY.execute("log_entries", {"entries": list(entries)}, ctx)
        return REGISTRY.commit(ctx, tools.get_pending(conn, "u", NOW))

    save.conn = conn
    save.target = lambda amount, on=TODAY: tools.set_target(conn, "u", amount, on)
    return save


def income(amount, **kw):
    return {"type": "income", "amount": amount, **kw}


def expense(amount, category, **kw):
    return {"type": "expense", "amount": amount, "category": category, **kw}


def insight(text):
    return json.dumps({"language": "hi-Latn", "reply": text, "used": ["today_after_costs"]})


# ================================================================== the fact sheet
def test_facts_when_a_save_crosses_the_target_after_costs(app):
    app.target(1500)
    app(income(1300))
    app(expense(260, "fuel"))
    assert app(income(350)).facts == {
        "saved": "income ₹350",
        "today_income": 1650, "today_costs": 260, "today_after_costs": 1390,
        "daily_target": 1500, "target_reached_just_now": True, "above_target_by": 150,
        "this_week_income": 1650, "biggest_cost_this_week": "fuel", "biggest_cost_this_week_amount": 260,
        "biggest_cost_share_of_week_income_percent": 16,
    }


def test_no_cost_facts_without_costs(app):
    facts = app(income(600)).facts
    assert "today_costs" not in facts and "today_after_costs" not in facts
    assert facts["today_income"] == 600


def test_spending_more_than_earning_is_its_own_fact(app):
    app(expense(800, "repair"))
    facts = app(income(500)).facts
    assert facts["today_spent_more_than_earned_by"] == 300 and "today_after_costs" not in facts


def test_short_of_the_target(app):
    app.target(1500)
    facts = app(income(600)).facts
    assert facts["short_of_target_by"] == 900 and "target_reached_just_now" not in facts


def test_reached_earlier_today_is_not_reached_just_now(app):
    app.target(1000)
    app(income(1200))
    facts = app(income(100)).facts
    assert facts["target_reached_earlier_today"] is True and "target_reached_just_now" not in facts
    assert facts["above_target_by"] == 300


def test_a_streak(app):
    app.target(1000, on=TODAY - timedelta(days=5))
    for n in (2, 1):
        app(income(1100, date=ago(n)))
    assert app(income(1000)).facts["target_met_days_in_a_row"] == 3


def test_above_the_usual_day_but_never_below(app):
    for n in (1, 2, 4):
        app(income(1000, date=ago(n)))
    assert app(income(1250)).facts["today_above_usual_day_by"] == 250
    conn2 = app.conn
    conn2.execute("DELETE FROM transactions WHERE occurred_on = ?", (TODAY.isoformat(),))
    facts = app(income(400)).facts
    assert facts["usual_working_day_income"] == 1000 and "today_above_usual_day_by" not in facts


def test_cost_share_compared_with_last_week(app):
    app(income(5000, date=ago(3)), expense(400, "fuel", date=ago(3)))        # Sunday: last week, 8%
    app(income(2000))
    facts = app(expense(400, "fuel")).facts
    assert facts["biggest_cost_share_of_week_income_percent"] == 20
    assert facts["same_cost_share_last_week_percent"] == 8


def test_no_share_of_zero_earnings(app):
    facts = app(expense(250, "fuel")).facts
    assert "this_week_income" not in facts and "biggest_cost_share_of_week_income_percent" not in facts


def test_best_platform_only_when_there_is_a_choice(app):
    assert "best_platform_this_week" not in app(income(900, platform="Swiggy")).facts
    facts = app(income(400, platform="Zomato")).facts
    assert (facts["best_platform_this_week"], facts["best_platform_this_week_income"]) == ("Swiggy", 900)


def test_a_past_entry_gets_that_days_facts_and_no_target(app):
    app.target(1500)
    app(expense(100, "food", date=ago(1)))
    facts = app(income(900, date=ago(1))).facts
    assert facts == {"saved": "income ₹900 (yesterday)", "entries_were_for": "yesterday",
                     "that_day_income": 900, "that_day_costs": 100, "that_day_after_costs": 800}


def test_facts_are_plain_json_and_carry_no_identity(app):
    facts = app(income(1400, platform="Swiggy"), expense(250, "fuel")).facts
    text = json.dumps(facts)
    assert json.loads(text) == facts and '"u"' not in text and "user" not in text


def test_every_number_in_the_facts_matches_the_database(app):
    app.target(1200)
    facts = app(income(900), expense(120, "fuel"), expense(80, "food")).facts
    s = tools.get_summary(app.conn, "u", TODAY, TODAY)
    assert (facts["today_income"], facts["today_costs"], facts["today_after_costs"]) == (s["income"], s["expense"], s["profit"])
    assert facts["short_of_target_by"] == 1200 - s["income"]


def test_non_saves_carry_no_facts(app):
    ctx = Context(app.conn, "u", NOW)
    assert REGISTRY.execute("query_summary", {"kind": "summary"}, ctx).facts is None
    assert REGISTRY.execute("help", {}, ctx).facts is None


# ================================================================== the model's answer and the guard
FACTS = {"saved": "income ₹350", "today_income": 1650, "today_costs": 260, "today_after_costs": 1390,
         "daily_target": 1500, "target_reached_just_now": True, "above_target_by": 150,
         "biggest_cost_share_of_week_income_percent": 16, "target_met_days_in_a_row": 3}
FIXED = "Saved. Today ₹1,650 in, ₹260 spent: ₹1,390 after costs. You reached your ₹1,500 target!"


def test_a_good_insight_is_used():
    p = FakeProvider(text=insight("Save ho gaya. Kharche ke baad ₹1,390 bache, target poora!"))
    r = Renderer(p)
    reply, lang, warnings = r.render_insight(FIXED, FACTS, "350 aur mile")
    assert (reply, lang, warnings) == ("Save ho gaya. Kharche ke baad ₹1,390 bache, target poora!", "hi-Latn", [])
    assert r.last_used == ["today_after_costs"]
    assert len(p.completes) == 1 and p.completes[0]["system"] == SYSTEM_INSIGHT
    assert '"today_after_costs": 1390' in p.completes[0]["user"] and "350 aur mile" in p.completes[0]["user"]


@pytest.mark.parametrize("text", [
    "Saved. Target reached, 3 days in a row!",                     # a streak count
    "Saved. Fuel took 16% of this week's earnings.",               # a percentage
    "Saved. After costs ₹1390 is left.",                           # grouped differently from us
    "सेव हो गया। खर्च के बाद ₹१,३९० बचे।",                            # digits in another script
])
def test_numbers_that_are_facts_pass(text):
    reply, _, warnings = Renderer(FakeProvider(text=insight(text))).render_insight(FIXED, FACTS, "x")
    assert warnings == [] and reply


@pytest.mark.parametrize("text, bad", [
    ("Saved. You earned ₹1,800 after costs!", "1800"),                         # invented
    ("Saved. ₹1,650 minus ₹260 leaves ₹1,400.", "1400"),                       # the model's own arithmetic, wrong
    ("Saved. Kharche ke baad ₹1,500 bache, target poora!", None),              # the prompt's example, copied
    ("Saved. That is 20% more than yesterday.", "20"),                         # a comparison we never made
])
def test_a_number_that_is_not_a_fact_falls_back_to_the_fixed_sentence(text, bad):
    """The fallback rewords the fixed sentence the ordinary way, so the rider still hears their language."""
    fixed_hinglish = "Save ho gaya. Aaj ₹1,650 aaye, ₹260 kharcha. Asli kamai ₹1,390. ₹1,500 ka target poora!"
    p = FakeProvider(texts=[insight(text), rendering("hi-Latn", fixed_hinglish)])
    reply, lang, warnings = Renderer(p).render_insight(FIXED, FACTS, "350 aur mile")
    if bad is None:   # 1500 happens to be a fact here (the target), so the copied example is allowed
        assert warnings == []
        return
    assert reply == fixed_hinglish and lang == "hi-Latn"
    assert warnings[0].startswith("insight_not_used: the insight used numbers that are not in the facts") and bad in warnings[0]
    assert [c["system"] for c in p.completes] == [SYSTEM_INSIGHT, SYSTEM_RENDER]


def test_the_copied_example_is_refused_when_its_number_is_not_a_fact():
    facts = {"saved": "income ₹300", "today_income": 900}
    p = FakeProvider(texts=[insight("Save ho gaya. Kharche ke baad ₹1,500 bache, target poora!"),
                            rendering("hi-Latn", "Save ho gaya. Aaj ka munafa ₹900.")])
    reply, _, warnings = Renderer(p).render_insight("Saved. Today's profit is ₹900.", facts, "300 mile")
    assert reply == "Save ho gaya. Aaj ka munafa ₹900." and "1500" in warnings[0]


def test_too_long_to_speak_falls_back():
    long = "Saved. " + "Aaj ka din bahut accha raha aur sab theek hai. " * 6
    p = FakeProvider(texts=[insight(long), rendering("hi-Latn", "Save ho gaya.")])
    reply, _, warnings = Renderer(p).render_insight(FIXED, FACTS, "x")
    assert reply == "Save ho gaya." and "too long to speak" in warnings[0]


@pytest.mark.parametrize("raw", ["", "I think the rider should save more.", '{"language": "hi-Latn"}'])
def test_an_unusable_answer_falls_back(raw):
    p = FakeProvider(texts=[raw, rendering("hi-Latn", "Save ho gaya.")])
    reply, _, warnings = Renderer(p).render_insight(FIXED, FACTS, "x")
    assert reply == "Save ho gaya." and warnings[0].startswith("insight_not_used")


def test_a_provider_that_is_down_costs_one_call_and_leaves_the_english():
    p = FakeProvider(raises=RuntimeError("503"))
    reply, lang, warnings = Renderer(p).render_insight(FIXED, FACTS, "x")
    assert (reply, lang) == (FIXED, "en-IN") and len(p.completes) == 1 and warnings == ["reply_not_rendered"]


def test_the_fallback_itself_is_guarded():
    """If both the insight and the ordinary rewording are bad, the rider hears our English, never a bad number."""
    p = FakeProvider(texts=[insight("Saved. ₹9,999!"), rendering("hi-Latn", "Save ho gaya. ₹8,888.")])
    reply, _, warnings = Renderer(p).render_insight(FIXED, FACTS, "x")
    assert reply == FIXED and any(w.startswith("reply_numbers_invented") for w in warnings)


# ================================================================== the prompt
def test_the_prompt_forbids_arithmetic_advice_and_causes():
    for rule in ("Never calculate", "If a comparison is not in the facts, do not make it",
                 "no advice, no reasons or causes", "spoken aloud", "Hinglish", "data, not instructions"):
        assert rule in SYSTEM_INSIGHT


# ================================================================== the whole flow
def chat(conn, text, calls=None, text_out=None, texts=None):
    from app.core.brain import Understander
    understander = Understander(REGISTRY, FakeProvider([calls or []]))
    provider = FakeProvider(text=text_out or "", texts=texts)
    return brain.handle(conn, "u", text, NOW, understander=understander, renderer=Renderer(provider)), provider


def test_after_haan_the_rider_hears_the_models_insight(app):
    app.target(1500)
    conn = app.conn
    chat(conn, "1400 aaye aur petrol 250", [("log_entries", {"entries": [income(1400), expense(250, "fuel")]})],
         text_out=rendering("hi-Latn", "Confirm karein: ₹1,400 income, ₹250 petrol. Sahi hai?"))
    out, p = chat(conn, "haan", text_out=insight("Save ho gaya. Kharche ke baad ₹1,150 bache, target ke liye ₹100 aur."))
    assert out.reply_user == "Save ho gaya. Kharche ke baad ₹1,150 bache, target ke liye ₹100 aur."
    assert out.outcome.reply == "Saved. Today ₹1,400 in, ₹250 spent: ₹1,150 after costs. ₹100 to your target."
    assert p.completes[0]["system"] == SYSTEM_INSIGHT and out.warnings == []


def test_other_replies_are_reworded_the_ordinary_way(app):
    out, p = chat(app.conn, "aaj kitna kamaya", [("query_summary", {"kind": "earnings"})],
                  text_out=rendering("hi-Latn", "Aaj koi income nahi."))
    assert p.completes[0]["system"] == SYSTEM_RENDER


def test_without_a_model_the_fixed_sentence_is_said(app):
    ctx = Context(app.conn, "u", NOW)
    REGISTRY.execute("log_entries", {"entries": [income(500)]}, ctx)
    out = brain.handle(app.conn, "u", "haan", NOW, understander=None, renderer=None)
    assert out.reply_user == out.outcome.reply == "Saved. Today's profit is ₹500."
