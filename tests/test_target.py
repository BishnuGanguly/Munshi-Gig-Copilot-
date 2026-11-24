from datetime import datetime, timedelta

import pytest

from app.core import db, dates, engine, tools

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=dates.IST)


@pytest.fixture
def chat():
    conn = db.connect(":memory:")
    db.ensure_user(conn, "u")
    clock = {"now": NOW}

    def say(text, user="u", **kw):
        return engine.handle(conn, user, text, clock["now"], **kw)

    say.conn, say.clock = conn, clock
    return say


def log(chat, text):
    chat(text)
    return chat("yes").reply


def test_no_target_yet(chat):
    assert chat("how far am I from my target").reply == "You have not set a daily target. Say for example: set my daily target to 1500."


def test_setting_a_target_needs_confirmation(chat):
    out = chat("set my daily target to 1500")
    assert out.reply == "Set your daily income target to ₹1,500? Say yes to confirm." and out.intent == "target"
    assert tools.get_target(chat.conn, "u", NOW.date()) is None
    assert chat("yes").reply == "Done. Your daily income target is ₹1,500."
    assert tools.get_target(chat.conn, "u", NOW.date()) == 1500


def test_progress_is_shown_after_each_save(chat):
    chat("set my daily target to 1500"), chat("yes")
    assert log(chat, "Got 600 from Swiggy") == "Saved. Today's profit is ₹600. Target: ₹600 of ₹1,500, ₹900 to go."
    assert log(chat, "Got 900 from Zomato") == "Saved. Today's profit is ₹1,500. You reached your daily target of ₹1,500!"
    assert log(chat, "Got 200 from Uber") == "Saved. Today's profit is ₹1,700. Target of ₹1,500 is already reached today."


def test_expense_only_saves_do_not_mention_the_target(chat):
    chat("set my daily target to 1500"), chat("yes")
    assert log(chat, "Spent 250 on petrol") == "Saved. Today's profit is -₹250."


def test_progress_question(chat):
    chat("set my daily target to 1500"), chat("yes")
    log(chat, "Got 600 from Swiggy")
    assert chat("how far am I from my target").reply == "Today ₹600 of ₹1,500. ₹900 to go."
    log(chat, "Got 1000 from Zomato")
    assert chat("did I hit my target?").reply == "Today ₹1,600 of ₹1,500. Target reached!"


def test_changing_the_target_keeps_history(chat):
    chat("set my daily target to 1500"), chat("yes")
    log(chat, "Got 1600 from Swiggy")                                  # day 1: target 1500 met
    chat.clock["now"] = NOW + timedelta(days=1)                         # day 2
    assert chat("my target is 2000").reply == "Change your daily income target from ₹1,500 to ₹2,000? Say yes to confirm."
    chat("yes")
    assert chat("did I hit my target yesterday").reply == "Yesterday ₹1,600 of ₹1,500. Target reached."   # judged by the OLD target
    assert chat("how far am I from my target").reply.startswith("Today ₹0 of ₹2,000.")


def test_target_in_force_is_chosen_by_date(chat):
    chat("set my daily target to 1500"), chat("yes")
    day1 = NOW.date()
    assert tools.get_target(chat.conn, "u", day1 - timedelta(days=1)) is None      # before it existed
    assert tools.get_target(chat.conn, "u", day1) == 1500


def test_streak(chat):
    chat("set my daily target to 1500"), chat("yes")
    for d in range(3):
        chat.clock["now"] = NOW + timedelta(days=d)
        log(chat, "Got 1600 from Swiggy")
    assert chat("how far am I from my target").reply == "Today ₹1,600 of ₹1,500. Target reached! Streak: 3 days."
    chat.clock["now"] = NOW + timedelta(days=3)                          # a new day, nothing earned yet: streak survives
    assert chat("how far am I from my target").reply == "Today ₹0 of ₹1,500. ₹1,500 to go. Streak: 3 days."
    chat.clock["now"] = NOW + timedelta(days=5)                          # a missed day breaks it
    assert "Streak" not in chat("how far am I from my target").reply


@pytest.mark.parametrize("text", ["set my daily target to 50", "set my daily target to 90000"])
def test_target_range(chat, text):
    assert chat(text).reply == "A daily target must be between ₹100 and ₹50,000."
    assert tools.get_pending(chat.conn, "u", NOW) is None


def test_same_target_is_a_no_op(chat):
    chat("set my daily target to 1500"), chat("yes")
    assert chat("set my daily target to 1500").reply == "Your daily income target is already ₹1,500."


def test_correcting_a_target_before_confirming(chat):
    chat("set my daily target to 1500")
    assert chat("make it 2000").reply == "Set your daily income target to ₹2,000? Say yes to confirm."
    chat("yes")
    assert tools.get_target(chat.conn, "u", NOW.date()) == 2000


def test_rejecting_a_target(chat):
    chat("set my daily target to 1500")
    assert "discarded" in chat("no").reply and tools.get_target(chat.conn, "u", NOW.date()) is None


def test_question_while_target_is_pending(chat):
    chat("set my daily target to 1500")
    assert chat("how much did I earn today").reply.endswith("still waiting for confirmation.")
    chat("yes")
    assert tools.get_target(chat.conn, "u", NOW.date()) == 1500


def test_other_ways_to_say_it(chat):
    assert chat("I want to earn 1800 a day").reply.startswith("Set your daily income target to ₹1,800")
    chat("no")
    assert chat("daily target 1200").reply.startswith("Set your daily income target to ₹1,200")


def test_money_reports_and_questions_are_not_settings(chat):
    out = chat("Got 1500 from Swiggy, target reached")
    assert "Please confirm: income ₹1,500 from Swiggy" in out.reply                 # logged, not a target change
    chat("no")
    chat("did I reach my target of 1500?")
    assert tools.get_pending(chat.conn, "u", NOW) is None


def test_targets_are_per_user(chat):
    db.ensure_user(chat.conn, "other")
    chat("set my daily target to 1500"), chat("yes")
    assert chat("how far am I from my target", user="other").reply.startswith("You have not set a daily target")
    chat("set my daily target to 900", user="other"), chat("yes", user="other")
    assert tools.get_target(chat.conn, "u", NOW.date()) == 1500 and tools.get_target(chat.conn, "other", NOW.date()) == 900


def test_message_audit_columns_are_filled(chat):
    from app import pipeline
    conn = db.connect(":memory:")
    pipeline.process(conn, "a", text="Got 1400 from Swiggy")
    row = conn.execute("SELECT intent, actions_json FROM messages").fetchone()
    assert row["intent"] == "log" and '"log_entries"' in row["actions_json"]
    assert db.recent_turns(conn, "a")[0]["intent"] == "log"
