import json
from pathlib import Path

import httpx
import pytest

from app.errors import TranslateUnavailableError
from app.translate.languages import HINGLISH, detect_text_language, is_english, normalize_code
from app.translate.llm import LlmTranslate
from app.translate.normalize import canonical_platforms, extract_numbers, normalize, numbers_preserved
from app.translate.numbers import DEV_COLLISIONS, ROM_COLLISIONS, normalize_digits, words_to_digits
from app.translate.sarvam import SarvamTranslate, split_text
from app.translate.service import LanguageService

DATA = json.loads((Path(__file__).parent / "data" / "sentences.json").read_text(encoding="utf-8"))["sentences"]


# ---------------------------------------------------------------- number words and digits
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


@pytest.mark.parametrize("native,ascii_", [("१४००", "1400"), ("১২০০", "1200"), ("௨௫௦", "250"), ("౮౦౦", "800"), ("೧೨೦೦", "1200")])
def test_native_digits(native, ascii_):
    assert normalize_digits(native) == ascii_


def test_platform_names_canonicalised():
    assert canonical_platforms("स्विगी से और ज़ोमैटो से") == "Swiggy से और Zomato से"
    assert canonical_platforms("swigi aur zomaato") == "Swiggy aur Zomato"
    assert canonical_platforms("scuba") == "scuba"          # no accidental matches inside words
    assert canonical_platforms("स्विगीकडून") == "Swiggyकडून"


def test_the_real_sarvam_transcript_from_testing():
    assert normalize("आज मैं दो सौ पचास रुपए का बचत किया।") == "आज मैं 250 रुपए का बचत किया।"


@pytest.mark.parametrize("item", DATA, ids=lambda i: f"{i['lang']}:{i['text'][:20]}")
def test_amounts_extracted_from_sample_sentences(item):
    assert extract_numbers(normalize(item["text"])) == item["numbers"]


def test_sample_sentence_counts():
    langs = {}
    for i in DATA:
        langs[i["lang"]] = langs.get(i["lang"], 0) + 1
    assert langs["hi-IN"] == langs["hi-Latn"] == langs["en-IN"] == 15
    assert all(langs[l] >= 6 for l in ("ta-IN", "kn-IN", "bn-IN", "te-IN", "mr-IN"))


def test_numbers_preserved():
    assert numbers_preserved("paid 1,400 and 250", "भुगतान 1400 और 250")[0]
    ok, missing = numbers_preserved("paid 1400 and 250", "paid 140")
    assert not ok and set(missing) == {"1400", "250"}


# ---------------------------------------------------------------- language detection
@pytest.mark.parametrize("text,last,expected", [
    ("आज 1400 मिले", None, "hi-IN"), ("আজ ১৪০০", None, "bn-IN"), ("இன்று 1400", None, "ta-IN"), ("ఈ రోజు", None, "te-IN"),
    ("ಇವತ್ತು", None, "kn-IN"), ("ਅੱਜ", None, "pa-IN"), ("આજે", None, "gu-IN"), ("आज 1400", "mr-IN", "mr-IN"),
    ("aaj swiggy se 1400 aaye", None, HINGLISH), ("kitna kamaya", "en-IN", HINGLISH),
    ("I earned 1400 today", None, "en-IN"), ("Show my expenses", "hi-IN", "en-IN"),
    ("1400", "hi-IN", "hi-IN"), ("1400", None, "en-IN"), ("1400", HINGLISH, HINGLISH),
])
def test_detect_text_language(text, last, expected):
    assert detect_text_language(text, last) == expected


def test_language_code_helpers():
    assert normalize_code("hi") == "hi-IN" and normalize_code("TA-in") == "ta-IN" and normalize_code("xx") is None
    assert normalize_code("hi-Latn") == HINGLISH
    assert is_english("en-IN") and not is_english("hi-IN")


# ---------------------------------------------------------------- Sarvam translate (mocked HTTP)
def sarvam(handler, **kw):
    return SarvamTranslate(api_key="k", transport=httpx.MockTransport(handler), retries=kw.pop("retries", 1), **kw)


def test_sarvam_translate_request_and_response():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["api-subscription-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"translated_text": "Got 1400 from Swiggy"})

    assert sarvam(handler).to_english("आज Swiggy से 1400 आए", "hi-IN") == "Got 1400 from Swiggy"
    assert seen["key"] == "k"
    assert seen["body"]["source_language_code"] == "hi-IN" and seen["body"]["target_language_code"] == "en-IN"
    assert seen["body"]["input"] == "आज Swiggy से 1400 आए"


def test_sarvam_retries_without_optional_fields_on_400():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(400, text="bad numerals_format") if "numerals_format" in bodies[-1] else \
            httpx.Response(200, json={"translated_text": "ok"})

    assert sarvam(handler).to_english("x", "hi-IN") == "ok"
    assert len(bodies) == 2 and "numerals_format" not in bodies[1]


def test_sarvam_hinglish_uses_hindi_source_and_roman_output():
    seen = []
    sarvam(lambda r: (seen.append(json.loads(r.content)), httpx.Response(200, json={"translated_text": "t"}))[1]
           ).from_english("Done", HINGLISH)
    assert seen[0]["target_language_code"] == "hi-IN" and seen[0]["output_script"] == "roman"


def test_sarvam_bad_key_and_missing_key():
    with pytest.raises(TranslateUnavailableError, match="API key"):
        sarvam(lambda r: httpx.Response(403)).to_english("x", "hi-IN")
    with pytest.raises(TranslateUnavailableError, match="SARVAM_API_KEY"):
        SarvamTranslate(transport=httpx.MockTransport(lambda r: httpx.Response(200))).to_english("x", "hi-IN")


def test_long_text_is_split():
    pieces = split_text(("यह एक वाक्य है। " * 400).strip(), 1800)
    assert len(pieces) > 1 and all(len(p) <= 1800 for p in pieces)


# ---------------------------------------------------------------- LLM provider (fake client)
class FakeMessages:
    def __init__(self, text="translated", stop="end_turn"):
        self.text, self.stop, self.calls = text, stop, []

    def create(self, **kw):
        self.calls.append(kw)
        block = type("B", (), {"type": "text", "text": self.text})()
        return type("R", (), {"content": [block], "stop_reason": self.stop})()


def test_llm_translate_prompt_and_output():
    msgs = FakeMessages("Got 1400 from Swiggy")
    llm = LlmTranslate(client=type("C", (), {"messages": msgs})())
    assert llm.to_english("aaj swiggy se 1400 aaye", HINGLISH) == "Got 1400 from Swiggy"
    call = msgs.calls[0]
    assert call["model"] == "claude-haiku-5-5" and "Hinglish" in call["system"] and call["messages"][0]["content"].startswith("aaj")


def test_llm_refusal_and_missing_key(monkeypatch):
    llm = LlmTranslate(client=type("C", (), {"messages": FakeMessages(stop="refusal")})())
    with pytest.raises(TranslateUnavailableError):
        llm.to_english("x", "hi-IN")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(TranslateUnavailableError):
        LlmTranslate().to_english("x", "hi-IN")


# ---------------------------------------------------------------- service (provider chain and number checks)
class Fake:
    def __init__(self, name, to=None, frm=None):
        self.name, self._to, self._from = name, to, frm

    def _go(self, v, text):
        if isinstance(v, Exception):
            raise v
        return v(text) if callable(v) else v

    def to_english(self, text, lang):
        return self._go(self._to, text)

    def from_english(self, text, lang):
        return self._go(self._from, text)


def test_english_skips_translation():
    svc = LanguageService([Fake("a", to=TranslateUnavailableError("never called"))])
    assert svc.to_english("Got 1400", "en-IN") == ("Got 1400", [])
    assert svc.from_english("Done", "en-IN") == ("Done", [])


def test_falls_back_when_numbers_are_dropped():
    svc = LanguageService([Fake("bad", to="Got money from Swiggy"), Fake("good", to="Got 1400 from Swiggy")])
    assert svc.to_english("आज Swiggy से 1400 आए", "hi-IN") == ("Got 1400 from Swiggy", [])


def test_warns_when_every_provider_drops_numbers():
    text, warnings = LanguageService([Fake("bad", to="Got money")]).to_english("1400 आए", "hi-IN")
    assert text == "Got money" and warnings[0].startswith("numbers_not_preserved")


def test_all_providers_down_raises():
    with pytest.raises(TranslateUnavailableError):
        LanguageService([Fake("a", to=TranslateUnavailableError("x"))]).to_english("आज", "hi-IN")


def test_reply_translation_never_raises():
    svc = LanguageService([Fake("a", frm=TranslateUnavailableError("down"))])
    text, warnings = svc.from_english("Today's profit is 1150", "hi-IN")
    assert text == "Today's profit is 1150" and "reply_not_translated" in warnings


def test_reply_digits_are_ascii():
    svc = LanguageService([Fake("a", frm="आज का मुनाफ़ा ११५० है")])
    assert svc.from_english("Today's profit is 1150", "hi-IN")[0] == "आज का मुनाफ़ा 1150 है"
