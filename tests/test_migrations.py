import sqlite3

from app.core import db


def test_fresh_database_is_at_the_latest_version():
    conn = db.connect(":memory:")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "transactions", "messages", "pending", "targets"} <= tables


def test_step3_database_is_upgraded_in_place(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE users (id TEXT PRIMARY KEY, last_language TEXT, created_at TEXT);
    CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, type TEXT NOT NULL
      CHECK (type IN ('income','expense')), amount REAL NOT NULL, category TEXT, platform TEXT, occurred_on TEXT, created_at TEXT);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, transcript TEXT, detected_language TEXT,
      english_text TEXT, reply_english TEXT, reply_user_language TEXT, latency_ms INTEGER, created_at TEXT);
    INSERT INTO users (id) VALUES ('u'); INSERT INTO transactions (user_id,type,amount) VALUES ('u','income',100);""")
    old.commit(), old.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT amount FROM transactions").fetchone()[0] == 100            # data kept
    conn.execute("INSERT INTO transactions (user_id,type,amount) VALUES ('u','saving',5)")  # 'saving' now allowed
    assert {"intent", "actions_json"} <= {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}


def test_reconnecting_is_idempotent(tmp_path):
    path = str(tmp_path / "x.db")
    db.connect(path).close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
