"""The action handlers, driven straight through the registry - no model involved.

This is where the behaviour that used to be tested through the rules engine lives now: nothing is stored
without confirmation, corrections and deletions are confirmed, and every figure a rider is told is computed
from the database.
"""
from datetime import datetime, timedelta

import pytest

from app.core import dates, db, tools, validate
from app.core.actions import REGISTRY
from app.core.registry import Context

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)   # a Wednesday


@pytest.fixture
def app():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    clock = {"now": NOW}

    def run(name, args=None, user="u"):
        return REGISTRY.execute(name, args or {}, Context(conn, user, clock["now"]))

    def confirm(user="u"):
        ctx = Context(conn, user, clock["now"])
        pending = tools.get_pending(conn, user, clock["now"])
        assert pending is not None, "nothing was waiting for confirmation"
        return REGISTRY.commit(ctx, pending)

    run.conn, run.clock, run.confirm = conn, clock, confirm
    return run


def saved(app, user="u"):
    return [dict(r) for r in app.conn.execute(
        "SELECT type, amount, category, platform, occurred_on FROM transactions WHERE user_id = ? ORDER BY id",
        (user,))]


def log(app, **entry):
    entry.setdefault("category", None)
    entry.setdefault("platform", None)
    return app("log_entries", {"entries": [entry]})


# ---------------------------------------------------------------- nothing is stored without a yes
def test_logging_proposes_then_commits(app):
    out = app("log_entries", {"entries": [
        {"type": "income", "amount": 1400, "platform": "Swiggy"},
        {"type": "expense", "amount": 250, "category": "fuel"}]})
    assert out.reply == "Please confirm: income ₹1,400 from Swiggy; expense ₹250 on fuel. Is that correct?"
    assert [e["status"] for e in out.entries] == ["pending_confirmation"] * 2
    assert saved(app) == []
    assert app.confirm().reply == "Saved. Today ₹1,400 in, ₹250 spent: ₹1,150 after costs."
    assert [(r["type"], r["amount"]) for r in saved(app)] == [("income", 1400), ("expense", 250)]


def test_a_missing_type_is_asked_for_not_guessed(app):
    out = log(app, amount=500)
    assert out.reply == "Was ₹500 income or an expense?"
    assert tools.get_pending(app.conn, "u", NOW)["awaiting"] == "type"
    assert saved(app) == []


@pytest.mark.parametrize("amount", [0, 0.5, 50_001, 90_000])
def test_amounts_outside_the_limits_are_refused(app, amount):
    assert "between ₹1 and ₹50,000" in log(app, type="expense", amount=amount, category="fuel").reply
    assert tools.get_pending(app.conn, "u", NOW) is None


def test_yesterday_is_stored_as_yesterday(app):
    app("log_entries", {"entries": [{"type": "income", "amount": 700, "platform": "Uber",
                                     "date": "2026-11-24"}]})
    app.confirm()
    assert saved(app)[0]["occurred_on"] == "2026-11-24"


def test_defaults_are_filled_in(app):
    """An entry from any source gets the same shape: today's date and a sensible category."""
    log(app, type="income", amount=900, platform="Zomato")
    app.confirm()
    assert saved(app)[0] == {"type": "income", "amount": 900, "category": "earnings",
                             "platform": "Zomato", "occurred_on": "2026-11-25"}
    log(app, type="saving", amount=100)
    app.confirm()
    assert saved(app)[1]["category"] == "savings"


# ---------------------------------------------------------------- correcting and deleting
def test_correcting_the_last_saved_entry_is_confirmed_first(app):
    log(app, type="expense", amount=250, category="fuel")
    app.confirm()
    out = app("correct_entry", {"amount": 300})
    assert "from expense ₹250 on fuel to expense ₹300 on fuel" in out.reply
    assert saved(app)[0]["amount"] == 250                      # not yet
    assert app.confirm().reply == "Updated. The last entry is now expense ₹300 on fuel."
    assert saved(app)[0]["amount"] == 300


def test_correcting_other_fields(app):
    log(app, type="expense", amount=250, category="fuel")
    app.confirm()
    app("correct_entry", {"category": "repair", "platform": "Swiggy"})
    app.confirm()
    assert saved(app)[0]["category"] == "repair" and saved(app)[0]["platform"] == "Swiggy"


def test_correcting_with_nothing_to_correct(app):
    assert app("correct_entry", {"amount": 300}).reply == "There is no saved entry to correct."
    assert app("correct_entry", {}).reply == "There is no saved entry to correct."
    log(app, type="expense", amount=250, category="fuel")
    app.confirm()
    assert app("correct_entry", {}).reply.startswith("What should I change?")   # nothing named to change


def test_correction_respects_the_amount_limits(app):
    log(app, type="expense", amount=250, category="fuel")
    app.confirm()
    assert "between ₹1 and ₹50,000" in app("correct_entry", {"amount": 90_000}).reply
    assert saved(app)[0]["amount"] == 250


def test_deleting_is_confirmed_first(app):
    assert app("delete_entry").reply == "You have no saved entries yet."
    log(app, type="expense", amount=250, category="fuel")
    app.confirm()
    assert "Delete the last entry (expense ₹250 on fuel)?" in app("delete_entry").reply
    assert len(saved(app)) == 1
    assert app.confirm().reply == "Deleted."
    assert saved(app) == []


# ---------------------------------------------------------------- every figure comes from the database
def test_queries(app):
    for e in ({"type": "income", "amount": 5000, "platform": "Swiggy"},
              {"type": "income", "amount": 3400, "platform": "Zomato"},
              {"type": "expense", "amount": 1200, "category": "fuel"},
              {"type": "expense", "amount": 900, "category": "repair"}):
        log(app, **e)
        app.confirm()
    app.clock["now"] = NOW + timedelta(days=1)
    q = lambda **kw: app("query_summary", kw).reply
    assert q(kind="earnings", period="this_week") == "This week you earned ₹8,400. Swiggy ₹5,000, Zomato ₹3,400."
    assert q(kind="spending", period="this_week") == "This week you spent ₹2,100. Biggest: fuel ₹1,200."
    assert q(kind="spending", period="this_week", category="fuel") == "This week you spent ₹1,200 on fuel."
    assert q(kind="profit", period="this_week") == "This week: income ₹8,400, expenses ₹2,100, profit ₹6,300."
    assert q(kind="best_platform", period="this_week") == \
        "Your best platform this week is Swiggy with ₹5,000. Next: Zomato ₹3,400."
    assert q(kind="earnings", period="today") == "No income recorded today."
    assert q(kind="earnings", period="yesterday").startswith("Yesterday you earned ₹8,400")
    assert q(kind="earnings", period="all").startswith("Overall you earned ₹8,400")


def test_savings_and_honest_gaps(app):
    log(app, type="saving", amount=250)
    app.confirm()
    assert app("query_summary", {"kind": "savings", "period": "today"}).reply == "Today you saved ₹250."
    assert "do not track working hours" in app("query_summary", {"kind": "hours"}).reply
    assert "coming soon" in app("query_summary", {"kind": "goals"}).reply


def test_empty_periods_say_so(app):
    for kind in ("earnings", "spending", "savings", "best_platform", "profit", "summary"):
        assert "No " in app("query_summary", {"kind": kind, "period": "this_month"}).reply


# ---------------------------------------------------------------- the daily target
def test_target_is_confirmed_and_kept_by_date(app):
    out = app("set_target", {"amount": 1500})
    assert out.reply == "Set your daily income target to ₹1,500? Say yes to confirm."
    assert tools.get_target(app.conn, "u", NOW.date()) is None
    app.confirm()
    assert tools.get_target(app.conn, "u", NOW.date()) == 1500
    assert tools.get_target(app.conn, "u", NOW.date() - timedelta(days=1)) is None


def test_target_progress_and_streak(app):
    app("set_target", {"amount": 1500}); app.confirm()
    assert app("get_target_progress").reply == "Today ₹0 of ₹1,500. ₹1,500 to go."
    log(app, type="income", amount=600, platform="Swiggy")
    assert app.confirm().reply == "Saved. Today ₹600 so far. ₹900 more to reach your ₹1,500 target."
    log(app, type="income", amount=900, platform="Zomato")
    assert app.confirm().reply == "Saved. You reached your daily target of ₹1,500!"
    assert app("get_target_progress").reply == "Today ₹1,500 of ₹1,500. Target reached!"
    for d in (1, 2):
        app.clock["now"] = NOW + timedelta(days=d)
        log(app, type="income", amount=1600, platform="Swiggy")
        app.confirm()
    assert app("get_target_progress").reply.endswith("Streak: 3 days.")


def test_past_days_are_judged_by_the_target_in_force_then(app):
    app("set_target", {"amount": 1500}); app.confirm()
    log(app, type="income", amount=1600, platform="Swiggy"); app.confirm()
    app.clock["now"] = NOW + timedelta(days=1)
    out = app("set_target", {"amount": 2000})
    assert "from ₹1,500 to ₹2,000" in out.reply
    app.confirm()
    assert app("get_target_progress", {"day": "yesterday"}).reply == \
        "Yesterday ₹1,600 of ₹1,500. Target reached."
    assert app("get_target_progress").reply.startswith("Today ₹0 of ₹2,000.")


def test_target_limits_and_no_op(app):
    for amount in (50, 90_000):
        assert app("set_target", {"amount": amount}).reply == \
            f"A daily target must be between ₹{validate.TARGET_MIN} and ₹50,000."
    app("set_target", {"amount": 1500}); app.confirm()
    assert app("set_target", {"amount": 1500}).reply == "Your daily income target is already ₹1,500."


def test_no_target_set(app):
    assert app("get_target_progress").reply.startswith("You have not set a daily target")


# ---------------------------------------------------------------- isolation and expiry
def test_riders_are_isolated(app):
    db.ensure_user(app.conn, "other")
    log(app, type="income", amount=900, platform="Zomato")
    app.confirm()
    assert app("query_summary", {"kind": "earnings"}, user="other").reply == "No income recorded today."
    app("set_target", {"amount": 1500}); app.confirm()
    app("set_target", {"amount": 900}, user="other"); app.confirm(user="other")
    assert tools.get_target(app.conn, "u", NOW.date()) == 1500
    assert tools.get_target(app.conn, "other", NOW.date()) == 900
    assert saved(app, "other") == []


def test_pending_actions_expire(app):
    log(app, type="expense", amount=250, category="fuel")
    app.clock["now"] = NOW + timedelta(minutes=tools.PENDING_MINUTES + 1)
    assert tools.get_pending(app.conn, "u", app.clock["now"]) is None
    assert saved(app) == []


def test_help(app):
    assert "Swiggy" in app("help").reply
