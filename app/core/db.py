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
    english_text TEXT,
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


def _migrate(conn: sqlite3.Connection) -> None:
    """Databases made by Step 1-3 only allow income/expense; rebuild the table to also allow 'saving'."""
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


def ensure_user(conn: sqlite3.Connection, user_id: str, language: str | None = None) -> None:
    conn.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
    if language:
        conn.execute("UPDATE users SET last_language = ? WHERE id = ?", (language, user_id))
    conn.commit()


def log_message(conn: sqlite3.Connection, user_id: str, **fields) -> None:
    conn.execute(
        """INSERT INTO messages (user_id, transcript, detected_language, english_text,
           reply_english, reply_user_language, latency_ms) VALUES (?,?,?,?,?,?,?)""",
        (user_id, fields.get("transcript"), fields.get("detected_language"), fields.get("english_text"),
         fields.get("reply_english"), fields.get("reply_user_language"), fields.get("latency_ms")),
    )
    conn.commit()
