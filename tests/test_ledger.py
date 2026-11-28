"""The ledger fixes and the two reports built on it: nothing a rider reported is lost before they confirm,
no impossible entry is stored, every filter they ask for is applied, and the daily and per-platform reports
add up from the database.
"""
from datetime import datetime, timedelta

import pytest

from app.core import dates, db, tools, validate
from app.core.actions import REGISTRY, merge_waiting
from app.core.registry import Context

NOW = datetime(2026, 11, 25, 21, 0, tzinfo=dates.IST)   # a Wednesday evening
TODAY = NOW.date()


@pytest.fixture
def app():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")

    def run(name, args=None, now=NOW):
        return REGISTRY.execute(name, args or {}, Context(conn, "u", now))

    def confirm(now=NOW):
        pending = tools.get_pending(conn, "u", now)
        assert pending is not None, "nothing was waiting for confirmation"
        return REGISTRY.commit(Context(conn, "u", now), pending)

    def log(*entries, now=NOW, **extra):
        out = run("log_entries", {"entries": list(entries), **extra}, now)
        confirm(now)
        return out

    run.conn, run.confirm, run.log = conn, confirm, log
    return run


def rows(app):
    return [tuple(r) for r in app.conn.execute(
        "SELECT type, amount, category, platform, occurred_on FROM transactions ORDER BY id")]


def ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def query(app, **args):
    return app("query_summary", args).reply


# ------------------------------------------------------------------ nothing reported is lost before "yes"
def test_a_new_entry_is_added_to_the_one_waiting(app):
    app("log_entries", {"entries": [{"type": "income", "amount": 300, "platform": "Uber"}]})
    out = app("log_entries", {"entries": [{"type": "expense", "amount": 20, "category": "food"}]})
    assert out.reply == "Please confirm: income ₹300 from Uber; expense ₹20 on food. Is that correct?"
    app.confirm()
    assert [r[:2] for r in rows(app)] == [("income", 300), ("expense", 20)]


def test_a_correction_replaces_what_was_waiting(app):
    app("log_entries", {"entries": [{"type": "expense", "amount": 250, "category": "fuel"}]})
    out = app("log_entries", {"entries": [{"type": "expense", "amount": 300, "category": "fuel"}], "replace_waiting": True})
    assert "₹250" not in out.reply and "₹300" in out.reply


def test_answering_income_or_expense_does_not_duplicate_the_entry(app):
    out = app("log_entries", {"entries": [{"amount": 500}]})
    assert out.reply == "Was ₹500 income or an expense?"
    out = app("log_entries", {"entries": [{"type": "income", "amount": 500}]})
    assert out.reply == "Please confirm: income ₹500. Is that correct?"


def test_restating_a_waiting_entry_does_not_duplicate_it():
    waiting = [{"type": "income", "amount": 300}, {"type": "expense", "amount": 20}]
    assert merge_waiting(waiting, [{"type": "income", "amount": 300}], replace=False) == \
        [{"type": "expense", "amount": 20}, {"type": "income", "amount": 300}]


def test_a_pending_target_is_not_merged_into_entries(app):
    app("set_target", {"amount": 1500})
    out = app("log_entries", {"entries": [{"type": "income", "amount": 300}]})
    assert out.reply == "Please confirm: income ₹300. Is that correct?"


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize("bad", ["kal", "yesterday", "2026-13-01", "2026-11-26", "2026-08-01"])
def test_an_impossible_date_is_refused_not_a_crash(app, bad):
    out = app("log_entries", {"entries": [{"type": "income", "amount": 500, "date": bad}]})
    assert "could not use that date" in out.reply
    assert tools.get_pending(app.conn, "u", NOW) is None


def test_a_real_past_date_is_fine(app):
    out = app("log_entries", {"entries": [{"type": "income", "amount": 500, "date": ago(1)}]})
    assert out.reply == "Please confirm: income ₹500 (yesterday). Is that correct?"


def test_date_limits():
    assert validate.parse_day(TODAY.isoformat(), TODAY) == TODAY
    assert validate.parse_day(ago(90), TODAY) is not None
    assert validate.parse_day(ago(91), TODAY) is None
    assert validate.parse_day(None, TODAY) is None


# ------------------------------------------------------------------ categories always fit the type
@pytest.mark.parametrize("type_, given, stored", [
    ("expense", "earnings", "other"), ("expense", "fuel", "fuel"), ("income", "fuel", "earnings"),
    ("income", "incentive", "incentive"), ("saving", "fuel", "savings"), ("expense", None, "other"),
])
def test_a_category_that_does_not_fit_is_replaced(app, type_, given, stored):
    app.log({"type": type_, "amount": 100, "category": given})
    assert rows(app)[0][2] == stored


def test_correcting_the_type_fixes_the_category(app):
    app.log({"type": "expense", "amount": 100, "category": "fuel"})
    app("correct_entry", {"type": "income"})
    app.confirm()
    assert rows(app)[0][:3] == ("income", 100, "earnings")


def test_correcting_only_the_amount_keeps_the_category(app):
    app.log({"type": "expense", "amount": 100, "category": "fuel"})
    app("correct_entry", {"amount": 120})
    app.confirm()
    assert rows(app)[0][:3] == ("expense", 120, "fuel")


# ------------------------------------------------------------------ platform on expenses
def test_a_platform_cost_is_read_back_with_its_platform(app):
    out = app("log_entries", {"entries": [{"type": "expense", "amount": 50, "category": "penalty", "platform": "Swiggy"}]})
    assert out.reply == "Please confirm: expense ₹50 on penalty for Swiggy. Is that correct?"


@pytest.fixture
def day(app):
    """Swiggy 1,400 and Zomato 600 earned; a Swiggy penalty of 50; fuel 250 shared."""
    app.log({"type": "income", "amount": 1400, "platform": "Swiggy"}, {"type": "income", "amount": 600, "platform": "Zomato"},
            {"type": "expense", "amount": 50, "category": "penalty", "platform": "Swiggy"},
            {"type": "expense", "amount": 250, "category": "fuel"})
    return app


# ------------------------------------------------------------------ every filter is applied
def test_spending_for_one_platform(day):
    assert query(day, kind="spending", platform="Swiggy") == "Today you spent ₹50 for Swiggy. Biggest: penalty ₹50."


def test_spending_on_a_category(day):
    assert query(day, kind="spending", category="fuel") == "Today you spent ₹250 on fuel."


def test_earnings_in_incentives(day):
    assert query(day, kind="earnings", category="incentive") == "No income in incentives recorded today."
    day.log({"type": "income", "amount": 200, "category": "incentive", "platform": "Swiggy"})
    assert query(day, kind="earnings", category="incentive") == "Today you earned ₹200 in incentives."


def test_earnings_from_one_platform(day):
    assert query(day, kind="earnings", platform="Zomato") == "Today you earned ₹600 from Zomato."


# ------------------------------------------------------------------ item 2: profit per platform
def test_one_platforms_own_profit(day):
    assert query(day, kind="profit", platform="Swiggy") == (
        "Today from Swiggy: earned ₹1,400, costs ₹50 (penalty), net ₹1,350. "
        "Shared costs of ₹250 (fuel) are not split between platforms.")


def test_a_platform_with_no_costs(day):
    assert query(day, kind="profit", platform="Zomato").startswith("Today from Zomato: earned ₹600.")


def test_a_platform_with_nothing(day):
    assert query(day, kind="profit", platform="Uber") == "Nothing recorded for Uber today."


def test_platform_breakdown_today(day):
    assert query(day, kind="platform_breakdown") == (
        "Today by platform: Swiggy net ₹1,350 (earned ₹1,400, costs ₹50 (penalty)); Zomato net ₹600. "
        "Shared costs of ₹250 (fuel) are not split between platforms.")


def test_platform_breakdown_over_a_week_is_per_working_day(app):
    # Monday to Wednesday: Swiggy on three days, Zomato on one big day. Totals favour Swiggy; per day, Zomato.
    for n, amount in ((2, 900), (1, 800), (0, 700)):
        app.log({"type": "income", "amount": amount, "platform": "Swiggy", "date": ago(n)})
    app.log({"type": "income", "amount": 1500, "platform": "Zomato", "date": ago(1)})
    assert query(app, kind="platform_breakdown", period="this_week") == (
        "This week by platform: Swiggy net ₹2,400, ₹800 a day over 3 days; Zomato net ₹1,500.")


def test_breakdown_numbers_add_up(day):
    b = tools.platform_breakdown(day.conn, "u", TODAY, TODAY)
    s = tools.get_summary(day.conn, "u", TODAY, TODAY)
    net = sum(p["net"] for p in b["platforms"].values()) - b["shared"]["total"]
    assert net == s["profit"] == 1700


def test_a_penalty_without_a_platform_is_a_shared_cost(app):
    app.log({"type": "income", "amount": 500, "platform": "Ola"}, {"type": "expense", "amount": 30, "category": "penalty"})
    b = tools.platform_breakdown(app.conn, "u", TODAY, TODAY)
    assert b["platforms"]["Ola"]["net"] == 500 and b["shared"]["total"] == 30


# ------------------------------------------------------------------ item 1: the end-of-day summary
def test_daily_summary(day):
    assert query(day, kind="summary") == (
        "Today: income ₹2,000 (Swiggy ₹1,400, Zomato ₹600). Expenses ₹300 (fuel ₹250, penalty ₹50). Profit ₹1,700.")


def test_daily_summary_with_target_and_recent_average(day):
    for n in (1, 2, 3):
        day.log({"type": "income", "amount": 1500, "platform": "Swiggy", "date": ago(n)})
    day("set_target", {"amount": 2500})
    day.confirm()
    assert query(day, kind="summary") == (
        "Today: income ₹2,000 (Swiggy ₹1,400, Zomato ₹600). Expenses ₹300 (fuel ₹250, penalty ₹50). Profit ₹1,700. "
        "₹500 short of your ₹2,500 target. Income is ₹500 above your recent daily average of ₹1,500.")


def test_the_average_ignores_days_off_and_needs_three_working_days(app):
    app.log({"type": "income", "amount": 1000, "date": ago(1)})
    app.log({"type": "income", "amount": 1000, "date": ago(4)})
    app.log({"type": "income", "amount": 1000})
    assert "average" not in query(app, kind="summary")          # only two earlier working days
    app.log({"type": "income", "amount": 1600, "date": ago(6)})
    assert tools.recent_daily_income(app.conn, "u", TODAY) == (1200, 3)
    assert query(app, kind="summary").endswith("Income is ₹200 below your recent daily average of ₹1,200.")


def test_yesterdays_summary_is_judged_by_yesterday(app):
    app.log({"type": "income", "amount": 900, "date": ago(1)})
    assert query(app, kind="summary", period="yesterday") == "Yesterday: income ₹900. No expenses. Profit ₹900."


def test_an_empty_day(app):
    assert query(app, kind="summary") == "No entries recorded today."


def test_a_weekly_summary_has_the_breakdown(day):
    assert query(day, kind="summary", period="this_week") == (
        "This week: income ₹2,000 (Swiggy ₹1,400, Zomato ₹600). Expenses ₹300 (fuel ₹250, penalty ₹50). Profit ₹1,700.")


def test_summary_for_one_platform_is_its_profit(day):
    assert query(day, kind="summary", platform="Swiggy").startswith("Today from Swiggy: earned ₹1,400")


# ------------------------------------------------------------------ schema
def test_migration_5_adds_the_index(app):
    names = {r[0] for r in app.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_transactions_user_day" in names
    assert app.conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
