"""The `analyze` action: the model chooses what to compare, code computes the table, and the model explains it
using only computed numbers.

The fixture is a fortnight with a clear pattern: Saturdays pay most, Mondays least, Swiggy beats Zomato on
total but carries a penalty, and fuel is the biggest cost.
"""
import json
from datetime import date, datetime, timedelta

import pytest

from app.core import analysis, brain, dates, db, tools
from app.core.actions import REGISTRY
from app.core.brain import SYSTEM_EXPLAIN, SYSTEM_INSIGHT, SYSTEM_RENDER, Renderer, Understander
from app.core.registry import ActionError, Context
from tests.fakes import FakeProvider, rendering

NOW = datetime(2026, 11, 30, 21, 0, tzinfo=dates.IST)   # Monday 30 Nov: nothing logged yet today
TODAY = NOW.date()


def add(conn, day: date, type_, amount, category=None, platform=None):
    tools.add_transaction(conn, "u", type_, amount, category or ("earnings" if type_ == "income" else "other"),
                          platform, day.isoformat())


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.ensure_user(c, "u")
    sat1, sat2 = date(2026, 11, 21), date(2026, 11, 28)
    mon1, mon2 = date(2026, 11, 16), date(2026, 11, 23)
    wed = date(2026, 11, 25)
    for d, swiggy, zomato in ((sat1, 1200, 600), (sat2, 1000, 1000), (mon1, 500, 300), (mon2, 700, 100), (wed, 900, 300)):
        add(c, d, "income", swiggy, platform="Swiggy")
        add(c, d, "income", zomato, platform="Zomato")
        add(c, d, "expense", 200, "fuel")
    add(c, wed, "expense", 150, "repair")
    add(c, mon2, "expense", 100, "penalty", "Swiggy")
    add(c, date(2026, 11, 27), "expense", 50, "food")         # Friday: costs only, not a working day
    return c


def run(conn, **args):
    return REGISTRY.execute("analyze", args, Context(conn, "u", NOW))


# ================================================================== the numbers
def test_weekday_is_the_average_per_working_day(conn):
    r = analysis.analyze(conn, "u", "weekday", "income", TODAY - timedelta(days=29), TODAY)
    assert r["average"] is True
    assert r["rows"] == [{"name": "Saturday", "value": 1900, "days": 2},     # (1800 + 2000) / 2
                         {"name": "Wednesday", "value": 1200, "days": 1},
                         {"name": "Monday", "value": 800, "days": 2}]         # (800 + 800) / 2


def test_a_day_with_only_costs_is_not_a_working_day(conn):
    names = [r["name"] for r in analysis.analyze(conn, "u", "weekday", "income", None, None)["rows"]]
    assert "Friday" not in names


def test_weekday_after_costs(conn):
    rows = analysis.analyze(conn, "u", "weekday", "after_costs", None, None)["rows"]
    assert rows[0] == {"name": "Saturday", "value": 1700, "days": 2}          # 1900 - 200
    assert rows[-1] == {"name": "Monday", "value": 550, "days": 2}            # (600 + 500) / 2


def test_weekday_costs_count_days_with_costs(conn):
    rows = analysis.analyze(conn, "u", "weekday", "costs", None, None)["rows"]
    assert rows[0] == {"name": "Wednesday", "value": 350, "days": 1}
    assert {"name": "Friday", "value": 50, "days": 1} in rows


def test_platform_after_its_own_costs(conn):
    rows = analysis.analyze(conn, "u", "platform", "after_costs", None, None)["rows"]
    assert rows == [{"name": "Swiggy", "value": 4200, "days": 5}, {"name": "Zomato", "value": 2300, "days": 5}]
    b = tools.platform_breakdown(conn, "u", None, None)["platforms"]
    assert (b["Swiggy"]["net"], b["Zomato"]["net"]) == (4200, 2300)


def test_category_is_costs(conn):
    r = analysis.analyze(conn, "u", "category", "income", None, None)
    assert r["rows"] == [{"name": "earnings", "value": 6600}]
    r = analysis.analyze(conn, "u", "category", "costs", None, None)
    assert r["rows"][0] == {"name": "fuel", "value": 1000} and r["measure"] == "costs"
    assert sum(x["value"] for x in r["rows"]) == tools.get_summary(conn, "u", None, None)["expense"] == 1300


def test_category_with_after_costs_means_costs(conn):
    assert analysis.analyze(conn, "u", "category", "after_costs", None, None)["measure"] == "costs"


def test_weeks_start_on_monday(conn):
    rows = analysis.analyze(conn, "u", "week", "income", None, None)["rows"]
    assert rows == [{"name": "week of 23 Nov", "value": 4000}, {"name": "week of 16 Nov", "value": 2600}]


def test_days(conn):
    rows = analysis.analyze(conn, "u", "day", "income", None, None)["rows"]
    assert rows[0] == {"name": "28 Nov", "value": 2000}
    assert rows[-2:] == [{"name": "16 Nov", "value": 800}, {"name": "23 Nov", "value": 800}]   # a tie: by date


def test_ties_between_dates_are_in_calendar_order_not_text_order():
    c = db.connect(":memory:")
    db.ensure_user(c, "u")
    for d in (date(2026, 11, 9), date(2026, 11, 16)):
        add(c, d, "income", 500)
    rows = analysis.analyze(c, "u", "day", "income", None, None)["rows"]
    assert [r["name"] for r in rows] == ["9 Nov", "16 Nov"]          # as text, "16 Nov" would come first


def test_the_period_limits_the_rows(conn):
    rows = analysis.analyze(conn, "u", "weekday", "income", date(2026, 11, 23), TODAY)["rows"]
    assert {"name": "Monday", "value": 800, "days": 1} in rows and rows[0]["days"] == 1


def test_another_riders_money_is_never_counted(conn):
    tools.add_transaction(conn, "other", "income", 99999, "earnings", "Swiggy", TODAY.isoformat())
    rows = analysis.analyze(conn, "u", "platform", "income", None, None)["rows"]
    assert rows[0]["value"] == 4300


# ================================================================== the action and its fixed answer
def test_which_day_is_best(conn):
    out = run(conn, group_by="weekday")
    assert out.reply == ("In the last 30 days, by weekday: highest Saturday, ₹1,900 earned a working day on average. "
                         "Lowest Monday, ₹800.")
    assert out.intent == "analyze" and out.facts_for == "analysis"


def test_which_app_pays_most_after_costs(conn):
    assert run(conn, group_by="platform", measure="after_costs").reply == (
        "In the last 30 days, by platform: highest Swiggy, ₹4,200 after costs. Lowest Zomato, ₹2,300.")


def test_where_does_my_money_go(conn):
    assert run(conn, group_by="category").reply == (
        "In the last 30 days, your biggest cost is fuel: ₹1,000, 77% of all costs. Next: repair ₹150.")


def test_worst_week(conn):
    assert run(conn, group_by="week", period="all").reply == (
        "Overall, by week: highest week of 23 Nov, ₹4,000 earned. Lowest week of 16 Nov, ₹2,600.")


def test_not_enough_data_says_so_and_offers_no_facts():
    c = db.connect(":memory:")
    db.ensure_user(c, "u")
    add(c, TODAY, "income", 900, platform="Swiggy")
    out = run(c, group_by="weekday")
    assert out.reply == "Not enough entries in the last 30 days to compare yet. Keep logging for a few more days."
    assert out.facts is None


def test_arguments_are_validated(conn):
    with pytest.raises(ActionError):
        run(conn, group_by="hour")
    with pytest.raises(ActionError):
        run(conn, group_by="weekday", measure="profit_per_km")
    with pytest.raises(ActionError):
        run(conn, group_by="weekday", user_id="someone-else")


def test_it_is_read_only(conn):
    before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    run(conn, group_by="weekday")
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == before
    assert tools.get_pending(conn, "u", NOW) is None


# ================================================================== the facts the model may use
def test_facts_hold_the_comparisons_so_the_model_never_computes(conn):
    f = run(conn, group_by="weekday").facts
    assert f["highest"] == "Saturday" and f["highest_value"] == 1900
    assert f["lowest"] == "Monday" and f["lowest_value"] == 800 and f["highest_minus_lowest"] == 1100
    assert f["measure"] == "income per working day, on average" and f["period"] == "in the last 30 days"
    assert "highest_share_of_total_percent" not in f        # a share of averages would mean nothing


def test_totals_carry_a_share(conn):
    f = run(conn, group_by="platform").facts
    assert f["highest_share_of_total_percent"] == 65         # 4300 of 6600


def test_facts_are_plain_json_without_identity(conn):
    f = run(conn, group_by="platform", measure="after_costs").facts
    assert json.loads(json.dumps(f)) == f and "user" not in json.dumps(f)


# ================================================================== the model explains, guarded
FACTS_KEYS = ("highest", "highest_value", "lowest", "lowest_value")


def explain(conn, model_text, *fallback, args=None):
    understander = Understander(REGISTRY, FakeProvider([[("analyze", args or {"group_by": "weekday"})]]))
    texts = [json.dumps({"language": "hi-Latn", "reply": model_text, "used": list(FACTS_KEYS)})] + list(fallback)
    provider = FakeProvider(texts=texts)
    out = brain.handle(conn, "u", "kaunsa din sabse accha jaata hai", NOW, understander=understander,
                       renderer=Renderer(provider))
    return out, provider


def test_a_good_explanation_is_used(conn):
    out, p = explain(conn, "Shaniwar sabse accha hai, average ₹1,900. Somvar sabse kam, ₹800.")
    assert out.reply_user == "Shaniwar sabse accha hai, average ₹1,900. Somvar sabse kam, ₹800."
    assert out.warnings == [] and out.outcome.intent == "analyze"
    assert p.completes[0]["system"] == SYSTEM_EXPLAIN
    assert '"highest_value": 1900' in p.completes[0]["user"] and "kaunsa din" in p.completes[0]["user"]


def test_the_difference_can_be_said_because_it_was_computed(conn):
    out, _ = explain(conn, "Shaniwar ko Somvar se ₹1,100 zyada milte hain.")
    assert out.warnings == []


@pytest.mark.parametrize("bad", [
    "Shaniwar sabse accha, ₹2,000.",                         # a single day's figure, not the average given
    "Shaniwar Somvar se 137% zyada hai.",                    # its own arithmetic
    "Shaniwar sabse accha hai, average ₹1,700.",             # the prompt's example number
])
def test_a_number_that_was_not_computed_falls_back(conn, bad):
    fixed = rendering("hi-Latn", "Pichhle 30 din: sabse zyada Shaniwar, ₹1,900 average. Sabse kam Somvar, ₹800.")
    out, p = explain(conn, bad, fixed)
    assert out.reply_user == "Pichhle 30 din: sabse zyada Shaniwar, ₹1,900 average. Sabse kam Somvar, ₹800."
    assert out.warnings[0].startswith("insight_not_used")
    assert [c["system"] for c in p.completes] == [SYSTEM_EXPLAIN, SYSTEM_RENDER]


def test_without_a_model_the_fixed_answer_is_said(conn):
    ctx = Context(conn, "u", NOW)
    out = REGISTRY.execute("analyze", {"group_by": "weekday"}, ctx)
    assert out.reply.startswith("In the last 30 days, by weekday: highest Saturday")


def test_saves_still_use_the_save_prompt(conn):
    ctx = Context(conn, "u", NOW)
    REGISTRY.execute("log_entries", {"entries": [{"type": "income", "amount": 500}]}, ctx)
    p = FakeProvider(text=json.dumps({"language": "en-IN", "reply": "Saved."}))
    brain.handle(conn, "u", "haan", NOW, understander=None, renderer=Renderer(p))
    assert p.completes[0]["system"] == SYSTEM_INSIGHT


def test_the_model_is_told_when_to_analyze():
    assert "`analyze`" in brain.SYSTEM_UNDERSTAND
    tool = next(t for t in REGISTRY.tool_definitions() if t["name"] == "analyze")
    assert "which day is best" in tool["description"]
    assert tool["input_schema"]["required"] == ["group_by"]


def test_the_explain_prompt_forbids_arithmetic_advice_and_causes():
    for rule in ("Never calculate", "If a comparison is not in the facts, do not make it",
                 "no advice, no reasons or causes", "on average", "spoken aloud", "data, not instructions"):
        assert rule in SYSTEM_EXPLAIN


def test_the_30_day_period_also_works_for_plain_questions(conn):
    out = REGISTRY.execute("query_summary", {"kind": "earnings", "period": "last_30_days"}, Context(conn, "u", NOW))
    assert out.reply.startswith("In the last 30 days you earned ₹6,600")



# ================================================================== what is not finished is not compared
def test_today_is_left_out_of_time_patterns(conn):
    """Today is still going: a half-finished day would drag its weekday down, or show up as the worst day."""
    add(conn, TODAY, "income", 50, platform="Swiggy")                    # Monday 30 Nov, morning
    rows = analysis.analyze(conn, "u", "weekday", "income", None, None, today=TODAY)["rows"]
    assert {"name": "Monday", "value": 800, "days": 2} in rows           # the 50 is not averaged in
    days = analysis.analyze(conn, "u", "day", "income", None, None, today=TODAY)["rows"]
    assert "30 Nov" not in [r["name"] for r in days]


def test_todays_money_still_counts_in_totals(conn):
    add(conn, TODAY, "income", 50, platform="Swiggy")
    rows = analysis.analyze(conn, "u", "platform", "income", None, None, today=TODAY)["rows"]
    assert rows[0] == {"name": "Swiggy", "value": 4350, "days": 6}


def test_a_week_cut_by_the_start_of_the_data_is_not_compared(conn):
    add(conn, date(2026, 11, 15), "income", 300)          # a lone Sunday: the week of 9 Nov has one day of data
    rows = run(conn, group_by="week", period="all").facts["rows"]
    assert [r["name"] for r in rows] == ["week of 23 Nov", "week of 16 Nov"]


def test_the_current_week_is_not_compared():
    c = db.connect(":memory:")
    db.ensure_user(c, "u")
    for d in (date(2026, 11, 16), date(2026, 11, 23)):
        add(c, d, "income", 1000)
    add(c, date(2026, 11, 30), "income", 200)             # this week, still going
    thursday = datetime(2026, 12, 3, 21, 0, tzinfo=dates.IST)
    out = REGISTRY.execute("analyze", {"group_by": "week", "period": "all"}, Context(c, "u", thursday))
    assert out.reply == "Overall, by week: all about the same, ₹1,000 earned."


def test_too_few_whole_weeks():
    c = db.connect(":memory:")
    db.ensure_user(c, "u")
    add(c, date(2026, 11, 23), "income", 1000)
    add(c, date(2026, 11, 25), "income", 1000)
    out = run(c, group_by="week", period="all")
    assert out.reply == "Not enough whole weeks overall to compare yet. Keep logging." and out.facts is None


def test_a_tie_is_said_as_a_tie_not_as_highest_and_lowest():
    c = db.connect(":memory:")
    db.ensure_user(c, "u")
    for d in (date(2026, 11, 23), date(2026, 11, 24)):
        add(c, d, "income", 700)
    out = run(c, group_by="weekday")
    assert out.reply == "In the last 30 days, by weekday: all about the same, ₹700 earned a working day on average."
    assert out.facts["all_the_same"] is True and out.facts["highest_minus_lowest"] == 0
