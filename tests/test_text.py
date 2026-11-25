"""Deterministic text preparation - the part of understanding that stays in code.

Spoken amounts become digits before the model ever sees a message. That is arithmetic, not language
understanding, and it is what lets the number guard in app/core/brain.py work at all.
"""
import json
from pathlib import Path

import pytest

from app.text import canonical_platforms, extract_numbers, normalize, normalize_digits, words_to_digits
from app.text.chunk import split_text
from app.text.numbers import DEV_COLLISIONS, ROM_COLLISIONS

DATA = json.loads((Path(__file__).parent / "data" / "sentences.json").read_text(encoding="utf-8"))["sentences"]


def test_no_ambiguous_number_words():
    assert DEV_COLLISIONS == [] and ROM_COLLISIONS == []


@pytest.mark.parametrize("text,expected", [
    ("दो सौ पचास", "250"), ("ढाई सौ", "250"), ("डेढ़ हज़ार", "1500"), ("साढ़े तीन सौ", "350"), ("सवा सौ", "125"),
    ("सवा दो सौ", "225"), ("पौने दो सौ", "175"), ("एक हज़ार दो सौ", "1200"), ("पाँच हज़ार दो सौ", "5200"),
    ("निन्यानवे", "99"), ("पचहत्तर", "75"), ("एक लाख पचास हज़ार", "150000"), ("सौ रुपये", "100 रुपये"),
    ("dedh hazaar", "1500"), ("dhai sau", "250"), ("saadhe teen sau", "350"), ("paanch sau", "500"),
    ("barah sau", "1200"), ("do hazaar paanch sau", "2500"), ("pachas rupaye", "50 rupaye"), ("bees", "20"),
    ("ek lakh", "100000"), ("sau", "100"), ("paune do sau", "175"), ("sawa sau", "125"),
])
def test_number_words(text, expected):
    assert words_to_digits(text) == expected


@pytest.mark.parametrize("text", ["मुझे दो", "mujhe do", "ek baar", "दो तीन चीज़ें", "साथ चलो", "so jao", "सो गया"])
def test_ordinary_words_are_not_turned_into_numbers(text):
    assert not any(ch.isdigit() for ch in words_to_digits(text))


def test_two_adjacent_units_are_not_added():
    assert "5" not in words_to_digits("दो तीन दिन")


@pytest.mark.parametrize("native,ascii_", [
    ("१४००", "1400"), ("১২০০", "1200"), ("௨௫௦", "250"), ("౮౦౦", "800"), ("೧೨೦೦", "1200")])
def test_native_digits(native, ascii_):
    assert normalize_digits(native) == ascii_


def test_platform_names_canonicalised():
    """Done so the model's `platform` enum matches, and so the grounding guard sees the same spelling."""
    assert canonical_platforms("स्विगी से और ज़ोमैटो से") == "Swiggy से और Zomato से"
    assert canonical_platforms("swigi aur zomaato") == "Swiggy aur Zomato"
    assert canonical_platforms("scuba") == "scuba"            # no accidental match inside a word
    assert canonical_platforms("स्विगीकडून") == "Swiggyकडून"


def test_the_real_sarvam_transcript():
    """A genuine transcript from a real voice note: the amount arrives as words, not digits."""
    assert normalize("आज मैं दो सौ पचास रुपए का बचत किया।") == "आज मैं 250 रुपए का बचत किया।"


@pytest.mark.parametrize("item", DATA, ids=lambda i: f"{i['lang']}:{i['text'][:20]}")
def test_amounts_extracted_from_sample_sentences(item):
    """Every amount in all 75 sample sentences, across 8 languages, survives preparation."""
    assert extract_numbers(normalize(item["text"])) == item["numbers"]


def test_sample_sentence_counts():
    langs = {}
    for i in DATA:
        langs[i["lang"]] = langs.get(i["lang"], 0) + 1
    assert langs["hi-IN"] == langs["hi-Latn"] == langs["en-IN"] == 15
    assert all(langs[l] >= 6 for l in ("ta-IN", "kn-IN", "bn-IN", "te-IN", "mr-IN"))


def test_split_text():
    pieces = split_text(("यह एक वाक्य है। " * 400).strip(), 1800)
    assert len(pieces) > 1 and all(len(p) <= 1800 for p in pieces)
    assert split_text("short", 1800) == ["short"]
    assert all(len(p) <= 10 for p in split_text("x" * 95, 10))     # one endless sentence: hard split
