from datetime import date

import pytest

from app.core import parser
from app.core.replies import fmt_money

TODAY = date(2026, 11, 25)


def parse(text):
    r = parser.parse_log(text, TODAY)
    return [(e["type"], e["amount"], e["category"], e["platform"], e["date"]) for e in r.entries], r.problems


T, Y = "2026-11-25", "2026-11-24"


@pytest.mark.parametrize("text,expected", [
    ("Got 1400 from Swiggy", [("income", 1400, "earnings", "Swiggy", T)]),
    ("Spent 250 on petrol", [("expense", 250, "fuel", None, T)]),
    ("Got 1400 from Swiggy and spent 250 on petrol", [("income", 1400, "earnings", "Swiggy", T), ("expense", 250, "fuel", None, T)]),
    ("Swiggy 900, Zomato 500, petrol 200", [("income", 900, "earnings", "Swiggy", T), ("income", 500, "earnings", "Zomato", T), ("expense", 200, "fuel", None, T)]),
    ("Yesterday I got 1400 from Swiggy and spent 250 on petrol", [("income", 1400, "earnings", "Swiggy", Y), ("expense", 250, "fuel", None, Y)]),
    ("Paid 500 for service and 100 for tea", [("expense", 500, "repair", None, T), ("expense", 100, "food", None, T)]),
    ("I saved 250 rupees today", [("saving", 250, "savings", None, T)]),
    ("Put 5 litres of petrol for 500", [("expense", 500, "fuel", None, T)]),
    ("Earned 1.5k from Uber", [("income", 1500, "earnings", "Uber", T)]),
    ("Got a fine of 500", [("expense", 500, "penalty", None, T)]),
    ("Received 1,400 rupees from zomato", [("income", 1400, "earnings", "Zomato", T)]),
    ("Got 200 bonus from Zepto", [("income", 200, "incentive", "Zepto", T)]),
    ("Bike EMI 5200 paid", [("expense", 5200, "emi", None, T)]),
    ("Delivered 12 orders and got 900 from Rapido", [("income", 900, "earnings", "Rapido", T)]),
    ("Rs 300 for recharge", [("expense", 300, "recharge", None, T)]),
    ("Zomato paid me 850", [("income", 850, "earnings", "Zomato", T)]),
    # romanised Hindi still works if it reaches the core untranslated
    ("aaj swiggy se 1400 aaye aur petrol 250 ka dala", [("income", 1400, "earnings", "Swiggy", T), ("expense", 250, "fuel", None, T)]),
])
def test_parse_log(text, expected):
    entries, problems = parse(text)
    assert entries == expected and problems == []


def test_no_amount_no_entry():
    assert parse("I had a good day") == ([], [])


def test_amount_range_and_missing_type():
    assert parse("spent 60000 on bike")[1] == ["amount_out_of_range"]
    assert parse("spent 0 on tea")[1] == ["amount_out_of_range"]
    entries, problems = parse("500")
    assert entries[0][0] is None and "needs_type" in problems


@pytest.mark.parametrize("text,new,old,field", [
    ("it was 300 not 250", 300, 250, None), ("not 250, 300", 300, 250, None), ("250 nahi 300", 300, 250, None),
    ("should be 300", 300, None, None), ("make it 1,500", 1500, None, None), ("no, 300", 300, None, None),
    ("actually 400", 400, None, None), ("change it to 350", 350, None, None),
])
def test_corrections_amount(text, new, old, field):
    c = parser.parse_correction(text)
    assert (c.new_amount, c.old_amount) == (new, old)


def test_corrections_fields_and_non_corrections():
    assert parser.parse_correction("actually it was diesel").category == "fuel"
    assert parser.parse_correction("no, it was income").type == "income"
    assert parser.parse_correction("it was Zomato not Swiggy").platform == "Zomato"
    assert parser.parse_correction("Got 1400 from Swiggy, not Zomato") is None   # a log, not a correction
    assert parser.parse_correction("Spent 250 on petrol") is None


@pytest.mark.parametrize("text,kind,period,extra", [
    ("How much did I earn this week", "earnings", "this_week", None),
    ("which platform is best", "best_platform", "this_week", None),
    ("show my expenses for last month", "spending", "last_month", None),
    ("what did I spend on petrol today", "spending", "today", "fuel"),
    ("what is my profit yesterday", "profit", "yesterday", None),
    ("give me a summary", "summary", "today", None),
    ("how much did I save this month", "savings", "this_month", None),
    ("how many hours did I work", "hours", "today", None),
    ("how much did I earn from Swiggy overall", "earnings", "all", "Swiggy"),
])
def test_queries(text, kind, period, extra):
    q = parser.parse_query(text)
    assert (q.kind, q.period) == (kind, period)
    if extra:
        assert extra in (q.category, q.platform)


def test_non_questions():
    assert parser.parse_query("Got 1400 from Swiggy") is None
    assert parser.parse_query("thanks") is None


@pytest.mark.parametrize("text,yes,no", [
    ("yes", True, False), ("Yeah, that's right", True, False), ("ok", True, False), ("haan sahi hai", True, False),
    ("👍", True, False), ("no", False, True), ("nope", False, True), ("wrong", False, True), ("cancel", False, True),
    ("no, 300", False, False), ("yes 300", False, False), ("it is not correct", False, True), ("hello", False, False),
])
def test_yes_no(text, yes, no):
    assert (parser.is_yes(text), parser.is_no(text)) == (yes, no)


def test_money_format_indian_grouping():
    assert [fmt_money(x) for x in (250, 1400, 125000, 1234567, 12.5, 0)] == ["₹250", "₹1,400", "₹1,25,000", "₹12,34,567", "₹12.50", "₹0"]
