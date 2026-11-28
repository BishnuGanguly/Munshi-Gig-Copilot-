"""One insight with every save: the most useful thing to hear right after logging money.

A voice reply has room for one fact, so this picks exactly one line, by a fixed priority, and code computes
every figure in it. The model never chooses or states an insight; it only rewords the sentence afterwards.

Most riders hear "₹1,650 today" and take it as what they earned, though fuel and other costs often eat a
large share (a Bengaluru diary study found running costs of ₹18,470 on ₹35,075 gross a month). So once today
has any costs, a save of income opens with what is left after them - "asli kamai" - and then the target.

Priority, for a save that includes today's income:
  1. the daily target was just reached (plus the streak, when there is one)
  2. the target was already reached: how far above it today is
  3. the target is not reached yet: how much is left
  4. no target: today is above the rider's usual working day (never said when below - the day is not over)
For a save of expenses only:
  5. that kind of expense this week, and its share of the week's earnings
Otherwise (or when nothing above applies):
  6. the profit of the day the entries were for
"""
from datetime import date, timedelta

from . import dates, tools
from .replies import fmt_money


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _profit_of(day: date, today: date) -> str:
    if day == today:
        return "Today's profit is"
    if day == today - timedelta(days=1):
        return "Yesterday's profit is"
    return f"Profit on {day.day} {_MONTHS[day.month - 1]} is"          # "20 Nov", easy to say aloud


def _after_costs(income: float, spent: float) -> str:
    net = income - spent
    left = (f"{fmt_money(net)} after costs" if net >= 0
            else f"{fmt_money(-net)} more spent than earned")
    return f"Today {fmt_money(income)} in, {fmt_money(spent)} spent: {left}."


def _with_costs(conn, user_id: str, today: date, income: float, spent: float, income_before: float) -> str:
    """The real-earnings line, plus the target in as few amounts as possible (a reply is heard, not read)."""
    text = _after_costs(income, spent)
    target = tools.get_target(conn, user_id, today)
    if target is None:
        return text
    if income >= target > income_before:
        streak = tools.target_streak(conn, user_id, today)
        return text + f" You reached your {fmt_money(target)} target!" + (f" {streak} days in a row." if streak >= 2 else "")
    if income >= target:
        return text + f" {fmt_money(income - target)} above your target."
    return text + f" {fmt_money(target - income)} to your target."


def _income_insight(conn, user_id: str, today: date, income_before: float) -> str | None:
    s = tools.get_summary(conn, user_id, today, today)
    income = s["income"]
    if s["expense"] > 0:
        return _with_costs(conn, user_id, today, income, s["expense"], income_before)
    target = tools.get_target(conn, user_id, today)
    if target is not None:
        if income >= target > income_before:
            text = f"You reached your daily target of {fmt_money(target)}!"
            streak = tools.target_streak(conn, user_id, today)
            return text + (f" {streak} days in a row." if streak >= 2 else "")
        if income >= target:
            return f"Today {fmt_money(income)} so far, {fmt_money(income - target)} above your target."
        return f"Today {fmt_money(income)} so far. {fmt_money(target - income)} more to reach your {fmt_money(target)} target."
    average, worked = tools.recent_daily_income(conn, user_id, today)
    if worked >= 3 and income - average >= 1:
        return f"Today {fmt_money(income)} so far, {fmt_money(income - average)} more than your usual day."
    return None


def _expense_insight(conn, user_id: str, today: date, category: str) -> str | None:
    s = tools.get_summary(conn, user_id, dates.week_start(today), today)
    spent = s["expense_by_category"].get(category, 0.0)
    if spent <= 0:
        return None
    what = "Expenses" if category == "other" else category[0].upper() + category[1:]
    text = f"{what} this week: {fmt_money(spent)}"
    if s["income"] > 0:
        share = round(100 * spent / s["income"])
        if share >= 1:
            return text + f", {share}% of what you earned."
    return text + "."


def after_save(conn, user_id: str, today: date, entries: list[dict], income_before: float) -> str:
    """The one line that follows "Saved." `income_before` is today's income before these entries."""
    today_iso = today.isoformat()
    todays = [e for e in entries if e["date"] == today_iso]
    if any(e["type"] == "income" for e in todays):
        line = _income_insight(conn, user_id, today, income_before)
        if line:
            return line
    elif todays and all(e["type"] == "expense" for e in todays):
        # the biggest expense just logged speaks for the rest
        biggest = max(todays, key=lambda e: e["amount"])
        line = _expense_insight(conn, user_id, today, biggest["category"] or "other")
        if line:
            return line
    day = date.fromisoformat(entries[0]["date"]) if len({e["date"] for e in entries}) == 1 else today
    profit = tools.get_summary(conn, user_id, day, day)["profit"]
    return f"{_profit_of(day, today)} {fmt_money(profit)}."
