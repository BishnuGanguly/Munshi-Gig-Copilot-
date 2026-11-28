"""Patterns in a rider's own money, for open questions: which weekday, platform, cost or week is highest or
lowest. The model chooses WHAT to look at (the `analyze` action's arguments); this module computes the table,
and the model then explains it using only the numbers computed here.

Averages are per working day - a day with income (or, for costs, a day with costs) - so a day off does not
make a weekday look worse than it is. Patterns over time leave out what is not finished: today is not
counted by weekday or day (it is still going), and only whole weeks are compared (a week cut by the start of
the period, or the current one, would always look like the worst).
"""
from datetime import date, timedelta

from . import dates, tools

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
GROUPS = ["weekday", "platform", "category", "week", "day"]
MEASURES = ["income", "costs", "after_costs"]
MIN_GROUPS = 2           # a comparison needs at least two things to compare


def say_day(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _whole(x: float):
    x = round(float(x))
    return int(x)


def _per_day(conn, user_id: str, start: date | None, end: date | None) -> dict[str, dict]:
    where, args = ["user_id = ?"], [user_id]
    if start:
        where.append("occurred_on >= ?")
        args.append(start.isoformat())
    if end:
        where.append("occurred_on <= ?")
        args.append(end.isoformat())
    rows = conn.execute(
        "SELECT occurred_on, SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS income, "
        "SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS costs "
        f"FROM transactions WHERE {' AND '.join(where)} GROUP BY occurred_on", args).fetchall()
    return {r["occurred_on"]: {"income": r["income"], "costs": r["costs"], "after_costs": r["income"] - r["costs"]}
            for r in rows}


def _counts(day: dict, measure: str) -> bool:
    """Whether a day is a working day for this measure."""
    return day["costs"] > 0 if measure == "costs" else day["income"] > 0


def analyze(conn, user_id: str, group_by: str, measure: str, start: date | None, end: date | None,
            today: date | None = None) -> dict:
    """Returns {"rows": [{"name", "value", "days"?}] highest first, "average": bool, "measure": str}.

    `value` is a total, except for weekdays, where it is the average per working day. Platform costs are only
    the costs logged against that platform (penalty, commission); category is about costs unless the measure
    is income."""
    rows: list[dict] = []
    average = False
    if group_by in ("weekday", "day", "week"):
        all_days = _per_day(conn, user_id, start, end)
        if today is not None:
            all_days.pop(today.isoformat(), None)                     # still going
        days = {d: v for d, v in all_days.items() if _counts(v, measure)}
        if group_by == "day":
            rows = [{"name": say_day(date.fromisoformat(d)), "value": _whole(v[measure]),
                     "_order": date.fromisoformat(d).toordinal()} for d, v in days.items()]
        elif group_by == "week":
            first = start or (min(date.fromisoformat(d) for d in all_days) if all_days else None)
            last = today - timedelta(days=1) if today is not None else end
            weeks: dict[date, float] = {}
            for d, v in days.items():
                w = dates.week_start(date.fromisoformat(d))
                if (first and w < first) or (last and w + timedelta(days=6) > last):
                    continue                                          # not a whole week inside the period
                weeks[w] = weeks.get(w, 0.0) + v[measure]
            rows = [{"name": f"week of {say_day(w)}", "value": _whole(t), "_order": w.toordinal()}
                    for w, t in weeks.items()]
        else:
            average = True
            by: dict[int, list[float]] = {}
            for d, v in days.items():
                by.setdefault(date.fromisoformat(d).weekday(), []).append(v[measure])
            rows = [{"name": WEEKDAYS[k], "value": _whole(sum(vs) / len(vs)), "days": len(vs), "_order": k}
                    for k, vs in by.items()]
    elif group_by == "platform":
        b = tools.platform_breakdown(conn, user_id, start, end)["platforms"]
        key = {"income": "income", "costs": "costs", "after_costs": "net"}[measure]
        rows = [{"name": name, "value": _whole(p[key]), "days": p["days"]} for name, p in b.items()
                if p[key] != 0 or measure != "costs"]
    elif group_by == "category":
        s = tools.get_summary(conn, user_id, start, end)
        if measure == "income":
            q = conn.execute(
                "SELECT category, SUM(amount) AS t FROM transactions WHERE user_id = ? AND type = 'income' "
                + ("AND occurred_on >= ? " if start else "") + ("AND occurred_on <= ? " if end else "")
                + "GROUP BY category", [user_id] + ([start.isoformat()] if start else []) + ([end.isoformat()] if end else []))
            rows = [{"name": r["category"] or "earnings", "value": _whole(r["t"])} for r in q]
        else:
            measure = "costs"
            rows = [{"name": c, "value": _whole(v)} for c, v in s["expense_by_category"].items()]
    # Ties go in calendar order (dates by date, weekdays Monday first), anything else by name.
    rows.sort(key=lambda r: (-r["value"], r.get("_order", 0), r["name"]))
    for r in rows:
        r.pop("_order", None)
    return {"rows": rows, "average": average, "measure": measure}


_MEASURE_WORD = {"income": "earned", "costs": "spent", "after_costs": "after costs"}


def summary_sentence(result: dict, group_by: str, label: str) -> str:
    """The fixed English answer: highest and lowest. Used without a model and as the model's fallback."""
    rows = result["rows"]
    if len(rows) < MIN_GROUPS:
        if group_by == "week":
            return f"Not enough whole weeks {label} to compare yet. Keep logging."
        return f"Not enough entries {label} to compare yet. Keep logging for a few more days."
    word = _MEASURE_WORD[result["measure"]]
    each = " a working day on average" if result["average"] else ""
    if group_by == "category" and result["measure"] == "costs":
        total = sum(r["value"] for r in rows) or 1
        top, nxt = rows[0], rows[1]
        return (f"{label[0].upper()}{label[1:]}, your biggest cost is {top['name']}: {fmt(top['value'])}, "
                f"{round(100 * top['value'] / total)}% of all costs. Next: {nxt['name']} {fmt(nxt['value'])}.")
    hi, lo = rows[0], rows[-1]
    if hi["value"] == lo["value"]:
        return f"{label[0].upper()}{label[1:]}, by {group_by}: all about the same, {fmt(hi['value'])} {word}{each}."
    return (f"{label[0].upper()}{label[1:]}, by {group_by}: highest {hi['name']}, {fmt(hi['value'])} {word}{each}. "
            f"Lowest {lo['name']}, {fmt(lo['value'])}.")


def facts(result: dict, group_by: str, label: str, limit: int = 7) -> dict:
    """What the model may say about the analysis. Comparisons it might want are computed here."""
    rows = result["rows"]
    out = {"period": label, "grouped_by": "whole week" if group_by == "week" else group_by,
           "measure": {"income": "income", "costs": "costs", "after_costs": "income after costs"}[result["measure"]]
           + (" per working day, on average" if result["average"] else " in total"),
           "rows": rows[:limit]}
    if len(rows) >= MIN_GROUPS:
        hi, lo = rows[0], rows[-1]
        if hi["value"] == lo["value"]:
            out["all_the_same"] = True
        out.update({"highest": hi["name"], "highest_value": hi["value"], "lowest": lo["name"],
                    "lowest_value": lo["value"], "highest_minus_lowest": hi["value"] - lo["value"]})
        total = sum(r["value"] for r in rows)
        if not result["average"] and total > 0 and all(r["value"] >= 0 for r in rows):
            out["highest_share_of_total_percent"] = round(100 * hi["value"] / total)
    else:
        out["not_enough_data"] = True
    return out


def fmt(n) -> str:
    from .replies import fmt_money
    return fmt_money(n)
