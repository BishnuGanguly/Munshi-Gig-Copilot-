"""SQLite storage: users, transactions, messages, pending (unconfirmed) actions."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    last_language TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    type TEXT NOT NULL CHECK (type IN ('income','expense','saving')),
    amount REAL NOT NULL,
    category TEXT,
    platform TEXT,
    occurred_on TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    transcript TEXT,
    detected_language TEXT,
    normalized_text TEXT,
    reply_english TEXT,
    reply_user_language TEXT,
    latency_ms INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pending (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _m1_allow_saving(conn: sqlite3.Connection) -> None:
    """Databases made by Steps 1-3 only allow income/expense; rebuild the table to also allow 'saving'."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'").fetchone()
    if row and "'saving'" not in row["sql"]:
        conn.executescript("""
            ALTER TABLE transactions RENAME TO transactions_old;
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id),
                type TEXT NOT NULL CHECK (type IN ('income','expense','saving')),
                amount REAL NOT NULL,
                category TEXT,
                platform TEXT,
                occurred_on TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO transactions SELECT * FROM transactions_old;
            DROP TABLE transactions_old;
        """)


def _m2_targets(conn: sqlite3.Connection) -> None:
    """Daily income targets with history: a past day is judged against the target in force on that day."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(id),
            amount REAL NOT NULL,
            effective_from TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_targets_user ON targets (user_id, effective_from);
    """)


def _m3_message_audit(conn: sqlite3.Connection) -> None:
    """Record which intent and actions each message produced (audit trail and conversation context)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if "intent" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN intent TEXT")
    if "actions_json" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN actions_json TEXT")


def _m4_rename_english_text(conn: sqlite3.Connection) -> None:
    """`messages.english_text` dates from the translate-to-English pipeline. Nothing is translated now:
    the column holds the normalised transcript the model was given, so give it that name."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if "english_text" in cols and "normalized_text" not in cols:
        conn.execute("ALTER TABLE messages RENAME COLUMN english_text TO normalized_text")
    elif "normalized_text" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN normalized_text TEXT")


# Append new migrations here with the next number. Never edit or reorder old ones.
MIGRATIONS = [(1, _m1_allow_saving), (2, _m2_targets), (3, _m3_message_audit),
              (4, _m4_rename_english_text)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, fn in MIGRATIONS:
        if number > version:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.commit()


def ensure_user(conn: sqlite3.Connection, user_id: str, language: str | None = None) -> None:
    conn.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
    if language:
        conn.execute("UPDATE users SET last_language = ? WHERE id = ?", (language, user_id))
    conn.commit()


def log_message(conn: sqlite3.Connection, user_id: str, **fields) -> None:
    conn.execute(
        """INSERT INTO messages (user_id, transcript, detected_language, normalized_text,
           reply_english, reply_user_language, latency_ms, intent, actions_json) VALUES (?,?,?,?,?,?,?,?,?)""",
        (user_id, fields.get("transcript"), fields.get("detected_language"), fields.get("normalized_text"),
         fields.get("reply_english"), fields.get("reply_user_language"), fields.get("latency_ms"),
         fields.get("intent"), fields.get("actions_json")),
    )
    conn.commit()


def recent_turns(conn: sqlite3.Connection, user_id: str, n: int = 6) -> list[dict]:
    """The user's last n exchanges, oldest first: context for the (future) LLM understanding layer."""
    rows = conn.execute(
        "SELECT normalized_text, reply_english, intent FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, n)).fetchall()
    return [dict(r) for r in reversed(rows)]
