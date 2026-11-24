"""Tool layer: every number the user sees is computed here, in code, never by a model."""
import json
import sqlite3
from datetime import date, datetime

from . import dates


# ------------------------------------------------------------------ transactions
def add_transaction(conn: sqlite3.Connection, user_id: str, type: str, amount: float, category: str | None,
                    platform: str | None, day: str) -> int:
    cur = conn.execute(
        "INSERT INTO transactions (user_id, type, amount, category, platform, occurred_on) VALUES (?,?,?,?,?,?)",
        (user_id, type, round(float(amount), 2), category, platform, day))
    conn.commit()
    return cur.lastrowid


def get_last_transaction(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    return dict(row) if row else None


def update_transaction(conn: sqlite3.Connection, user_id: str, tx_id: int, **fields) -> bool:
    allowed = {"type", "amount", "category", "platform", "occurred_on"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(f"UPDATE transactions SET {sets} WHERE id = ? AND user_id = ?", (*fields.values(), tx_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def delete_transaction(conn: sqlite3.Connection, user_id: str, tx_id: int) -> bool:
    cur = conn.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
    conn.commit()
    return cur.rowcount > 0


# ------------------------------------------------------------------ summaries
def get_summary(conn: sqlite3.Connection, user_id: str, start: date | None, end: date | None,
                category: str | None = None, platform: str | None = None) -> dict:
    """Totals for a period. Returns income, expense, saving, profit, plus income_by_platform and
    expense_by_category (descending)."""
    where, args = ["user_id = ?"], [user_id]
    if start:
        where.append("occurred_on >= ?")
        args.append(start.isoformat())
    if end:
        where.append("occurred_on <= ?")
        args.append(end.isoformat())
    if category:
        where.append("category = ?")
        args.append(category)
    if platform:
        where.append("platform = ?")
        args.append(platform)
    rows = conn.execute(f"SELECT type, amount, category, platform FROM transactions WHERE {' AND '.join(where)}", args).fetchall()
    totals = {"income": 0.0, "expense": 0.0, "saving": 0.0}
    by_platform: dict[str, float] = {}
    by_category: dict[str, float] = {}
    for r in rows:
        totals[r["type"]] += r["amount"]
        if r["type"] == "income" and r["platform"]:
            by_platform[r["platform"]] = by_platform.get(r["platform"], 0.0) + r["amount"]
        if r["type"] == "expense":
            by_category[r["category"] or "other"] = by_category.get(r["category"] or "other", 0.0) + r["amount"]
    return {
        **{k: round(v, 2) for k, v in totals.items()},
        "profit": round(totals["income"] - totals["expense"], 2),
        "count": len(rows),
        "income_by_platform": dict(sorted(((k, round(v, 2)) for k, v in by_platform.items()), key=lambda kv: -kv[1])),
        "expense_by_category": dict(sorted(((k, round(v, 2)) for k, v in by_category.items()), key=lambda kv: -kv[1])),
    }


# ------------------------------------------------------------------ pending (unconfirmed) actions
PENDING_MINUTES = 30


def set_pending(conn: sqlite3.Connection, user_id: str, payload: dict, now: datetime) -> None:
    conn.execute("INSERT OR REPLACE INTO pending (user_id, payload, created_at) VALUES (?,?,?)",
                 (user_id, json.dumps(payload), now.isoformat()))
    conn.commit()


def get_pending(conn: sqlite3.Connection, user_id: str, now: datetime) -> dict | None:
    """The user's unconfirmed action, or None. Anything older than PENDING_MINUTES is dropped."""
    row = conn.execute("SELECT payload, created_at FROM pending WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    age = (now - datetime.fromisoformat(row["created_at"])).total_seconds() / 60
    if age > PENDING_MINUTES:
        clear_pending(conn, user_id)
        return None
    return json.loads(row["payload"])


def clear_pending(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute("DELETE FROM pending WHERE user_id = ?", (user_id,))
    conn.commit()
