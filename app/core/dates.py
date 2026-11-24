"""Dates in Indian Standard Time, and period resolution (today, this week ...)."""
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset: no tz database needed (Windows-friendly)


def now_ist() -> datetime:
    return datetime.now(IST)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def month_start(d: date) -> date:
    return d.replace(day=1)


def resolve_period(key: str, today: date) -> tuple[str, date | None, date | None]:
    """Return (label, start, end) inclusive. start/end None means all time."""
    if key == "yesterday":
        d = today - timedelta(days=1)
        return "yesterday", d, d
    if key == "this_week":
        return "this week", week_start(today), today
    if key == "last_week":
        start = week_start(today) - timedelta(days=7)
        return "last week", start, start + timedelta(days=6)
    if key == "this_month":
        return "this month", month_start(today), today
    if key == "last_month":
        end = month_start(today) - timedelta(days=1)
        return "last month", month_start(end), end
    if key == "all":
        return "overall", None, None
    return "today", today, today
