"""The one insight said after every save: right line, right priority, every figure from the database."""
from datetime import datetime, timedelta

import pytest

from app.core import dates, db, tools
from app.core.actions import REGISTRY
from app.core.registry import Context
from app.text import normalize_digits

NOW = datetime(2026, 11, 25, 21, 0, tzinfo=dates.IST)   # a Wednesday; the week began on Monday the 23rd
TODAY = NOW.date()


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def app():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")

    def save(*entries, now=NOW):
        """Log the entries and confirm; returns what the rider hears after "haan"."""
        ctx = Context(conn, "u", now)
        REGISTRY.execute("log_entries", {"entries": list(entries)}, ctx)
        return REGISTRY.commit(ctx, tools.get_pending(conn, "u", now)).reply

    def target(amount, on=TODAY):
        tools.set_target(conn, "u", amount, on)

    save.conn, save.target = conn, target
    return save


def income(amount, **kw):
    return {"type": "income", "amount": amount, **kw}


def expense(amount, category, **kw):
    return {"type": "expense", "amount": amount, "category": category, **kw}


# ------------------------------------------------------------------ income, with a target
def test_target_just_reached(app):
    app.target(1500)
    app(income(1000))
    assert app(income(600)) == "Saved. You reached your daily target of ₹1,500!"


def test_target_reached_with_a_streak(app):
    app.target(1500, on=TODAY - timedelta(days=5))
    for n in (2, 1):
        app(income(1600, date=ago(n)))
    assert app(income(1500)) == "Saved. You reached your daily target of ₹1,500! 3 days in a row."


def test_already_above_the_target(app):
    app.target(1500)
    app(income(1600))
    assert app(income(200)) == "Saved. Today ₹1,800 so far, ₹300 above your target."


def test_still_short_of_the_target(app):
    app.target(1500)
    assert app(income(600)) == "Saved. Today ₹600 so far. ₹900 more to reach your ₹1,500 target."


# ------------------------------------------------------------------ income, no target
def test_better_than_a_usual_day(app):
    for n in (1, 2, 4):
        app(income(1000, date=ago(n)))
    assert app(income(1250)) == "Saved. Today ₹1,250 so far, ₹250 more than your usual day."


def test_below_a_usual_day_is_not_said_the_day_is_not_over(app):
    for n in (1, 2, 4):
        app(income(1000, date=ago(n)))
    assert app(income(400)) == "Saved. Today's profit is ₹400."


def test_no_comparison_without_three_working_days(app):
    for n in (1, 2):
        app(income(1000, date=ago(n)))
    assert app(income(1250)) == "Saved. Today's profit is ₹1,250."


def test_the_first_entry_ever(app):
    assert app(income(500)) == "Saved. Today's profit is ₹500."


# ------------------------------------------------------------------ expenses only
def test_expense_share_of_the_weeks_earnings(app):
    app(income(2000, date=ago(1)), income(2000))          # Tuesday and Wednesday: this week
    app(income(9000, date=ago(3)))                         # Sunday: last week, not counted
    app(expense(300, "fuel", date=ago(1)))
    assert app(expense(500, "fuel")) == "Saved. Fuel this week: ₹800, 20% of what you earned."


def test_expense_with_no_earnings_this_week(app):
    assert app(expense(250, "fuel")) == "Saved. Fuel this week: ₹250."


def test_uncategorised_expense(app):
    app(income(1000))
    assert app(expense(100, "other")) == "Saved. Expenses this week: ₹100, 10% of what you earned."


def test_several_expenses_speak_about_the_biggest(app):
    app(income(1000))
    assert app(expense(50, "food"), expense(200, "repair")) == "Saved. Repair this week: ₹200, 20% of what you earned."


# ------------------------------------------------------------------ mixed and past entries
def test_income_wins_over_expense_in_one_message(app):
    app.target(1500)
    assert app(income(1400), expense(250, "fuel")) == "Saved. Today ₹1,400 so far. ₹100 more to reach your ₹1,500 target."


def test_a_past_entry_reports_that_days_profit(app):
    app.target(1500)
    app(expense(100, "food", date=ago(1)))
    assert app(income(900, date=ago(1))) == "Saved. Yesterday's profit is ₹800."


def test_an_older_entry(app):
    assert app(income(700, date=ago(5))) == "Saved. Profit on 20 Nov is ₹700."


# ------------------------------------------------------------------ voice-first: short, and every figure is real
@pytest.mark.parametrize("setup, entry", [
    ("target", income(600)), ("target", income(1600)), ("history", income(1250)), ("none", expense(500, "fuel")),
])
def test_every_insight_is_short_enough_to_hear(app, setup, entry):
    if setup == "target":
        app.target(1500)
    if setup == "history":
        for n in (1, 2, 4):
            app(income(1000, date=ago(n)))
    reply = app(entry)
    assert len(reply) <= 120                                   # a few seconds of speech
    assert normalize_digits(reply).count("₹") <= 3             # at most three amounts in one breath


# ------------------------------------------------------------------ the rewording guard accepts these sentences
@pytest.mark.parametrize("english, hinglish", [
    ("Saved. Fuel this week: ₹800, 20% of what you earned.", "Save ho gaya. Is hafte petrol ₹800, kamai ka 20%."),
    ("Saved. Today ₹1,250 so far, ₹250 more than your usual day.", "Save ho gaya. Aaj ab tak ₹1,250, roz se ₹250 zyada."),
    ("Saved. You reached your daily target of ₹1,500! 3 days in a row.", "Save ho gaya. ₹1,500 ka target poora! 3 din lagataar."),
])
def test_a_faithful_rewording_is_used(english, hinglish):
    from app.core.brain import Renderer
    from tests.fakes import FakeProvider, rendering
    reply, lang, warnings = Renderer(FakeProvider(text=rendering("hi-Latn", hinglish))).render(english, "x")
    assert (reply, warnings) == (hinglish, [])


def test_a_rewording_that_changes_the_percentage_is_refused():
    from app.core.brain import Renderer
    from tests.fakes import FakeProvider, rendering
    english = "Saved. Fuel this week: ₹800, 20% of what you earned."
    reply, _, warnings = Renderer(FakeProvider(text=rendering("hi-Latn", "Petrol ₹800, kamai ka 25%."))).render(english, "x")
    assert reply == english and warnings[0].startswith("reply_numbers_invented")
