import json
from datetime import datetime, timedelta

import pytest

from app.core import db, dates, engine, tools
from app.core.llm_parser import LlmLogParser

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)  # a Wednesday


@pytest.fixture
def chat():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    clock = {"now": NOW}

    def say(text, **kw):
        return engine.handle(conn, "u", text, clock["now"], **kw)

    say.conn, say.clock = conn, clock
    return say


def saved(chat):
    return [dict(r) for r in chat.conn.execute("SELECT type, amount, category, platform, occurred_on FROM transactions ORDER BY id")]


def test_nothing_saved_until_confirmed(chat):
    o = chat("Got 1400 from Swiggy and spent 250 on petrol")
    assert "Please confirm" in o.reply and "₹1,400" in o.reply and saved(chat) == []
    assert [e["status"] for e in o.entries] == ["pending_confirmation"] * 2
    o = chat("yes")
    assert o.reply == "Saved. Today's profit is ₹1,150."
    assert [(r["type"], r["amount"]) for r in saved(chat)] == [("income", 1400), ("expense", 250)]


def test_no_discards(chat):
    chat("Spent 250 on petrol")
    assert "discarded" in chat("no").reply and saved(chat) == []
    assert "nothing" in chat("yes").reply.lower()


def test_correct_pending_amount_before_saving(chat):
    chat("Spent 250 on petrol")
    o = chat("it was 300 not 250")
    assert "₹300" in o.reply and "Please confirm" in o.reply
    chat("yes")
    assert saved(chat)[0]["amount"] == 300


def test_correct_pending_with_several_entries_needs_old_amount(chat):
    chat("Got 1400 from Swiggy and spent 250 on petrol")
    assert "Which entry" in chat("should be 300").reply
    assert "₹300" in chat("250 nahi 300").reply
    chat("yes")
    assert [r["amount"] for r in saved(chat)] == [1400, 300]


def test_correct_saved_entry_needs_confirmation(chat):
    chat("Spent 250 on petrol"), chat("yes")
    o = chat("should be 300")
    assert "from expense ₹250 on fuel to expense ₹300 on fuel" in o.reply and saved(chat)[0]["amount"] == 250
    assert chat("yes").reply.startswith("Updated")
    assert saved(chat)[0]["amount"] == 300


def test_delete_last_needs_confirmation(chat):
    assert chat("delete last entry").reply == "You have no saved entries yet."
    chat("Spent 250 on petrol"), chat("yes")
    assert "Delete the last entry" in chat("undo").reply and len(saved(chat)) == 1
    assert chat("yes").reply == "Deleted." and saved(chat) == []


def test_undo_with_pending_just_discards(chat):
    chat("Got 900 from Zomato"), chat("yes")
    chat("Spent 100 on tea")
    assert "discarded" in chat("undo").reply and len(saved(chat)) == 1


def test_missing_type_is_asked_not_guessed(chat):
    o = chat("500")
    assert o.reply == "Was ₹500 income or an expense?"
    assert "Please confirm: expense ₹500" in chat("it was an expense").reply
    chat("yes")
    assert saved(chat)[0]["type"] == "expense"


def test_bad_amount_rejected(chat):
    assert "between ₹1 and ₹50,000" in chat("spent 90000 on bike").reply
    assert saved(chat) == []


def test_new_log_replaces_pending_and_says_so(chat):
    chat("Spent 250 on petrol")
    o = chat("Spent 100 on tea")
    assert o.reply.startswith("Your earlier unsaved entry was dropped.")
    chat("yes")
    assert [r["amount"] for r in saved(chat)] == [100]


def test_question_leaves_pending_alone(chat):
    chat("Spent 250 on petrol")
    o = chat("how much did I earn today")
    assert "still waiting" in o.reply
    chat("yes")
    assert len(saved(chat)) == 1


def test_pending_expires(chat):
    chat("Spent 250 on petrol")
    chat.clock["now"] = NOW + timedelta(minutes=tools.PENDING_MINUTES + 1)
    assert "nothing" in chat("yes").reply.lower() and saved(chat) == []


def test_queries_and_numbers_come_from_the_database(chat):
    for t in ["Got 5000 from Swiggy", "Got 3400 from Zomato", "Spent 1200 on petrol", "Spent 900 on service"]:
        chat(t), chat("yes")
    chat.clock["now"] = NOW + timedelta(days=1)
    assert chat("how much did I earn this week").reply == "This week you earned ₹8,400. Swiggy ₹5,000, Zomato ₹3,400."
    assert chat("how much did I spend this week").reply == "This week you spent ₹2,100. Biggest: fuel ₹1,200."
    assert chat("what did I spend on petrol this week").reply == "This week you spent ₹1,200 on fuel."
    assert chat("what is my profit this week").reply == "This week: income ₹8,400, expenses ₹2,100, profit ₹6,300."
    assert chat("which platform is best").reply == "Your best platform this week is Swiggy with ₹5,000. Next: Zomato ₹3,400."
    assert chat("how much did I earn today").reply == "No income recorded today."
    assert chat("how much did I earn yesterday").reply.startswith("Yesterday you earned")


def test_savings_and_unsupported_questions(chat):
    chat("I saved 250 rupees"), chat("yes")
    assert chat("how much did I save today").reply == "Today you saved ₹250."
    assert "do not track working hours" in chat("how many hours did I work").reply
    assert "coming soon" in chat("how is my goal").reply


def test_yesterday_entry_and_help_and_unknown(chat):
    chat("Yesterday I got 700 from Uber"), chat("yes")
    assert saved(chat)[0]["occurred_on"] == "2026-11-24"
    assert "Swiggy" in chat("hello").reply
    assert chat("what is the weather").intent == "unknown"


def test_users_are_isolated(chat):
    db.ensure_user(chat.conn, "other")
    chat("Got 900 from Zomato"), chat("yes")
    assert engine.handle(chat.conn, "other", "how much did I earn today", NOW).reply == "No income recorded today."
    assert engine.handle(chat.conn, "other", "yes", NOW).reply.startswith("There is nothing")


# ---------------------------------------------------------------- optional LLM fallback
class FakeMessages:
    def __init__(self, text):
        self.text, self.calls = text, []

    def create(self, **kw):
        self.calls.append(kw)
        block = type("B", (), {"type": "text", "text": self.text})()
        return type("R", (), {"content": [block], "stop_reason": "end_turn"})()


def fake_llm(text):
    msgs = FakeMessages(text)
    return LlmLogParser(client=type("C", (), {"messages": msgs})()), msgs


def test_llm_not_called_when_rules_succeed(chat):
    llm, msgs = fake_llm(json.dumps({"entries": []}))
    chat("Spent 250 on petrol", llm=llm)
    assert msgs.calls == []


def test_llm_output_is_validated_and_garbage_ignored():
    d = NOW.date()
    clean = LlmLogParser._clean
    assert clean('noise {"entries": [{"type": "expense", "amount": 300, "category": "fuel", "platform": "swiggy", "day": "yesterday"}]} x', d) == [
        {"type": "expense", "amount": 300.0, "category": "fuel", "platform": "Swiggy", "date": "2026-11-24"}]
    assert clean('{"entries": [{"type": "income", "amount": 999999}]}', d) == []                 # out of range
    assert clean('{"entries": [{"type": "bogus", "amount": 100}]}', d) == []                      # bad type
    assert clean('{"entries": [{"type": "income", "amount": "abc"}]}', d) == []
    assert clean("not json", d) == [] and clean('{"entries": "x"}', d) == []


def test_llm_failure_never_crashes(chat):
    class Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("network down")
    o = chat("whatever 77 stuff happened", llm=LlmLogParser(client=Boom()))
    assert o.intent in ("unknown", "log")


def test_llm_fallback_end_to_end(chat):
    llm, _ = fake_llm(json.dumps({"entries": [{"type": "income", "amount": 77, "category": "earnings", "platform": None, "day": "today"}]}))
    # no amount phrase the rules accept as money -> "77" with a quantity unit is skipped by the rules
    o = chat("77 orders worth of trouble", llm=llm)
    assert "llm_parser_used" in o.warnings and "Please confirm: income ₹77" in o.reply


# ---------------------------------------------------------------- database migration
def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE users (id TEXT PRIMARY KEY, last_language TEXT, created_at TEXT);
    CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, type TEXT NOT NULL
      CHECK (type IN ('income','expense')), amount REAL NOT NULL, category TEXT, platform TEXT, occurred_on TEXT, created_at TEXT);
    INSERT INTO users (id) VALUES ('u'); INSERT INTO transactions (user_id,type,amount) VALUES ('u','income',100);""")
    old.commit(), old.close()
    conn = db.connect(path)
    tools.add_transaction(conn, "u", "saving", 50, "savings", None, "2026-11-25")
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
