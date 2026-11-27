"""The deterministic rules that stay in code: yes/no, amount limits, entry validation.

yes/no is the one piece of language handling that runs *before* the model, so it has to be both cheap and
unable to swallow a message that belongs to the model.
"""
import pytest

from app.core import validate


@pytest.mark.parametrize("text", [
    "yes", "Yes", "yeah", "yep", "ok", "okay", "sure", "correct", "right", "confirm", "save it",
    "that's right", "it is correct", "haan", "haan theek hai", "ha ji", "sahi hai", "theek hai", "👍", "✓",
    "yes please", "ok done", "yes that is correct"])
def test_agreement(text):
    assert validate.is_yes(text) and not validate.is_no(text)


@pytest.mark.parametrize("text", [
    "no", "No", "nope", "nahi", "nahin", "galat", "wrong", "incorrect", "cancel", "discard", "stop", "👎",
    "nahi nahi", "no no", "that's wrong", "it is wrong", "galat hai", "not correct", "not right", "ye sahi nahi hai",
    "sahi nahi hai", "no that is wrong", "nahi ye galat hai"])
def test_refusal(text):
    assert validate.is_no(text) and not validate.is_yes(text)


@pytest.mark.parametrize("text", [
    # contain a refusal word, but belong to the model
    "kuch samajh nahi aaya", "mujhe nahi pata", "petrol ka kharcha nahi hua", "stop the music", "I am not sure",
    "wrong platform", "cancel my last entry", "no idea", "not today", "no it was income", "nahi 300 tha",
    "delete the last one", "undo",
    # contain an agreement word, but belong to the model
    "ok show me today's earnings", "yes and also spent 100 on tea", "sure how much did I earn",
    # nothing
    "", "   ", "hello", "Swiggy se 1400 aaye"])
def test_neither_agreement_nor_refusal(text):
    assert not validate.is_yes(text) and not validate.is_no(text)


@pytest.mark.parametrize("text", ["yes 300", "no, 300", "haan 250", "nahi 250 nahi 300", "300"])
def test_anything_with_a_number_is_for_the_model(text):
    """A digit means the rider is stating or correcting an amount, never just agreeing."""
    assert not validate.is_yes(text) and not validate.is_no(text)


def test_long_messages_are_never_a_bare_answer():
    assert not validate.is_yes("yes yes yes yes yes yes yes") and not validate.is_no("no no no no no no no")


def test_a_refusal_word_wins_over_an_agreement_word():
    assert not validate.is_yes("not correct") and validate.is_no("not correct")


# ---------------------------------------------------------------- entry validation
@pytest.mark.parametrize("entry,problems", [
    ({"type": "income", "amount": 1400}, []),
    ({"type": "expense", "amount": 250, "category": "fuel"}, []),
    ({"type": "saving", "amount": 1}, []),
    ({"type": "income", "amount": 50_000}, []),
    ({"type": "income", "amount": 0}, ["amount_out_of_range"]),
    ({"type": "income", "amount": 50_001}, ["amount_out_of_range"]),
    ({"type": "income", "amount": None}, ["amount_out_of_range"]),
    ({"type": "income"}, ["amount_out_of_range"]),
    ({"type": None, "amount": 500}, ["needs_type"]),
    ({"amount": 500}, ["needs_type"]),
    ({"type": "gift", "amount": 500}, ["needs_type"]),
    ({"type": "expense", "amount": 250, "category": "lottery"}, ["bad_category"]),
    ({"type": "expense", "amount": 250}, ["bad_category"]),
    ({"type": "expense", "amount": 0, "category": "lottery"}, ["amount_out_of_range", "bad_category"]),
])
def test_validate_entry(entry, problems):
    assert validate.validate_entry(entry) == problems


def test_the_limits():
    assert (validate.MIN_AMOUNT, validate.MAX_AMOUNT) == (1, 50_000)
    assert (validate.TARGET_MIN, validate.TARGET_MAX) == (100, 50_000)
