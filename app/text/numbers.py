"""Spoken Hindi number words -> digits (Devanagari and Roman script), plus digit normalisation.

Speech-to-text writes amounts as words ("दो सौ पचास"), and translation models can drop or change them,
so we convert words to digits BEFORE translating. Handles 1-99, sau/hazaar/lakh/crore, and the
fraction words dhai (2.5), dedh (1.5), sawa (+.25), saadhe (+.5), paune (-.25).

Safety rule: a lone small number word (दो = two / "give") is only converted when it is clearly an
amount: the run contains a multiplier or fraction, is 10 or more, or is followed by a currency/unit word.
Regional-language number words (Tamil, Bengali ...) are NOT handled here; their digits are.
"""
import re
import unicodedata

# ---------------------------------------------------------------- key functions

def dkey(word: str) -> str:
    """Devanagari spelling key: ignore nukta, chandrabindu and anusvara variants."""
    d = unicodedata.normalize("NFD", word)
    d = d.replace("़", "").replace("ँ", "").replace("ं", "")
    return unicodedata.normalize("NFC", d)


def rkey(word: str) -> str:
    """Roman spelling key: lowercase, collapse vowel and consonant doubling (aa->a, ee->i, tt->t, chh->ch)."""
    w = word.lower()
    w = w.replace("ee", "i").replace("ii", "i").replace("oo", "u").replace("uu", "u").replace("aa", "a")
    w = w.replace("chh", "ch")
    return re.sub(r"([b-df-hj-np-tv-z])\1", r"\1", w)


# ---------------------------------------------------------------- vocabulary

_DEV_UNITS = """
1 एक;2 दो;3 तीन;4 चार;5 पाँच पांच;6 छह छः छे छ;7 सात;8 आठ;9 नौ;10 दस;11 ग्यारह;12 बारह;13 तेरह;14 चौदह;
15 पंद्रह पन्द्रह;16 सोलह;17 सत्रह;18 अठारह;19 उन्नीस;20 बीस;21 इक्कीस;22 बाईस;23 तेईस;24 चौबीस;
25 पच्चीस;26 छब्बीस;27 सत्ताईस;28 अट्ठाईस अठाईस;29 उनतीस;30 तीस;31 इकतीस;32 बत्तीस;33 तैंतीस तेंतीस;
34 चौंतीस;35 पैंतीस;36 छत्तीस;37 सैंतीस;38 अड़तीस;39 उनतालीस;40 चालीस;41 इकतालीस;42 बयालीस;
43 तैंतालीस;44 चौवालीस चवालीस;45 पैंतालीस;46 छियालीस;47 सैंतालीस;48 अड़तालीस;49 उनचास;50 पचास;
51 इक्यावन;52 बावन;53 तिरपन तिरेपन;54 चौवन;55 पचपन;56 छप्पन;57 सत्तावन;58 अट्ठावन अठावन;59 उनसठ;
60 साठ;61 इकसठ;62 बासठ;63 तिरसठ;64 चौंसठ;65 पैंसठ;66 छियासठ;67 सड़सठ;68 अड़सठ;69 उनहत्तर;70 सत्तर;
71 इकहत्तर;72 बहत्तर;73 तिहत्तर;74 चौहत्तर;75 पचहत्तर;76 छिहत्तर;77 सतहत्तर;78 अठहत्तर;79 उनासी;
80 अस्सी;81 इक्यासी;82 बयासी;83 तिरासी;84 चौरासी;85 पचासी;86 छियासी;87 सत्तासी;88 अट्ठासी अठासी;
89 नवासी;90 नब्बे;91 इक्यानवे;92 बानवे;93 तिरानवे;94 चौरानवे;95 पचानवे;96 छियानवे;97 सत्तानवे;
98 अट्ठानवे अठानवे;99 निन्यानवे निन्यानबे
"""

_ROM_UNITS = """
1 ek;2 do;3 teen tin;4 char chaar;5 paanch panch;6 chhe chhah chah che;7 saat sat;8 aath ath;9 nau;10 das;
11 gyarah gyaarah;12 barah baarah;13 terah;14 chaudah;15 pandrah pandra;16 solah sola;17 satrah;
18 atharah athara;19 unnis unnees unis;20 bees bis;21 ikkis ikkees ikis;22 baais bais baees;23 teis teyis;
24 chaubis chaubees;25 pachchis pachis pachees;26 chhabbis chhabis;27 sattais satais;28 atthais athais athaais;
29 untees untis unatis;30 tees tis;31 ikatis iktis;32 battis batis;33 taintis tentis tetis;34 chauntis chautis;
35 paintis paitis;36 chhattis chattis;37 saintis saitis;38 adtis artis adatis;39 unchalis untalis;
40 chalis chaalis;41 iktalis ikatalis;42 bayalis byalis;43 taintalis tentalis;44 chauwalis chavalis;
45 paintalis pentalis;46 chhiyalis chiyalis;47 saintalis saitalis;48 adtalis artalis;49 unchas unanchas;
50 pachas pachaas;51 ikyavan ikyawan;52 bavan baavan;53 tirpan trepan tirepan;54 chauvan chauwan;
55 pachpan;56 chhappan chappan;57 sattavan satavan;58 atthavan athavan;59 unsath unasath;
61 iksath ikasath;62 baasath basath;63 tirsath tirasath;64 chaunsath chausath;65 painsath paisath;
66 chhiyasath chiyasath;67 sadsath;68 adsath arsath;69 unhattar unahattar;70 sattar satar;71 ikhattar ikahattar;
72 bahattar bahatar;73 tihattar;74 chauhattar;75 pachhattar pachattar;76 chhihattar chihattar;
77 satahattar sathattar;78 athahattar athhattar;79 unasi unnasi;80 assi;81 ikyasi;82 bayasi byasi;83 tirasi;
84 chaurasi;85 pachasi;86 chhiyasi chiyasi;87 sattasi satasi;88 atthasi athasi;89 navasi nawasi;
90 nabbe nabbay;91 ikyanve ikyanbe;92 baanve banve;93 tiranve;94 chauranve;95 pachanve;96 chhiyanve;
97 sattanve;98 atthanve athanve;99 ninyanve ninanve nanve
"""

_DEV_MULT = {"सौ": 100, "हज़ार": 1000, "हजार": 1000, "लाख": 100000, "करोड़": 10_000_000, "करोड": 10_000_000}
_ROM_MULT = {"sau": 100, "sao": 100, "hazaar": 1000, "hazar": 1000, "hajar": 1000, "hajaar": 1000,
             "lakh": 100000, "lakhs": 100000, "lac": 100000, "laakh": 100000, "crore": 10_000_000, "karod": 10_000_000}

# fixed fractions (value), and prefix words (delta applied to the NEXT unit)
_DEV_FIXED = {"ढाई": 2.5, "अढ़ाई": 2.5, "डेढ़": 1.5, "डेढ": 1.5, "पौन": 0.75}
_ROM_FIXED = {"dhai": 2.5, "adhai": 2.5, "dedh": 1.5, "paun": 0.75}
_DEV_PREFIX = {"सवा": 0.25, "साढ़े": 0.5, "साढे": 0.5, "साड़े": 0.5, "पौने": -0.25}
_ROM_PREFIX = {"sawa": 0.25, "sava": 0.25, "saadhe": 0.5, "sadhe": 0.5, "saarhe": 0.5, "paune": -0.25, "pone": -0.25}

# words that, right after a small number, show it is an amount or a quantity
_UNIT_WORDS = {
    "रुपये", "रुपए", "रुपया", "रुपयों", "रु", "रूपये", "रूपए", "रुपिये", "घंटे", "घंटा", "घण्टे", "किलो", "लीटर", "दिन",
    "rupaye", "rupees", "rupee", "rupaya", "rupiye", "rs", "ghante", "ghanta", "kilo", "liter", "litre", "din",
}


def _build(table: str, key) -> tuple[dict[str, int], list[str]]:
    mapping: dict[str, int] = {}
    collisions: list[str] = []
    for part in table.replace("\n", " ").split(";"):
        part = part.strip()
        if not part:
            continue
        num, *words = part.split()
        for w in words:
            k = key(w)
            if k in mapping and mapping[k] != int(num):
                collisions.append(f"{w!r}: {mapping[k]} vs {num}")
            mapping[k] = int(num)
    return mapping, collisions


DEV_UNITS, DEV_COLLISIONS = _build(_DEV_UNITS, dkey)
ROM_UNITS, ROM_COLLISIONS = _build(_ROM_UNITS, rkey)
_DEV_MULT_K = {dkey(k): v for k, v in _DEV_MULT.items()}
_ROM_MULT_K = {rkey(k): v for k, v in _ROM_MULT.items()}
_DEV_FIXED_K = {dkey(k): v for k, v in _DEV_FIXED.items()}
_ROM_FIXED_K = {rkey(k): v for k, v in _ROM_FIXED.items()}
_DEV_PREFIX_K = {dkey(k): v for k, v in _DEV_PREFIX.items()}
_ROM_PREFIX_K = {rkey(k): v for k, v in _ROM_PREFIX.items()}
_UNIT_WORDS_K = {dkey(w) for w in _UNIT_WORDS} | {rkey(w) for w in _UNIT_WORDS}


def _classify(token: str):
    """Return (kind, value) for a number-ish token, or None. kind: unit|mult|fixed|prefix."""
    if any("ऀ" <= ch <= "ॿ" for ch in token):
        k = dkey(token)
        for kind, table in (("unit", DEV_UNITS), ("mult", _DEV_MULT_K), ("fixed", _DEV_FIXED_K), ("prefix", _DEV_PREFIX_K)):
            if k in table:
                return kind, table[k]
        return None
    if token.isascii() and token.isalpha():
        k = rkey(token)
        for kind, table in (("unit", ROM_UNITS), ("mult", _ROM_MULT_K), ("fixed", _ROM_FIXED_K), ("prefix", _ROM_PREFIX_K)):
            if k in table:
                return kind, table[k]
    return None


def _evaluate(run: list[tuple[str, float]]) -> float | None:
    """Combine a run of classified tokens into a number, or None if the grammar is invalid."""
    total, current, pending = 0.0, 0.0, None
    last = None
    for i, (kind, val) in enumerate(run):
        if kind == "prefix":
            if pending is not None or last == "unit":
                return None
            pending = val
        elif kind == "unit":
            if last in ("unit", "fixed"):
                return None
            current += val + (pending or 0.0)
            pending = None
        elif kind == "fixed":
            if last in ("unit", "fixed") or pending is not None:
                return None
            current += val
        else:  # multiplier
            if pending is not None:  # "sawa sau" = 1.25 * 100, "paune sau" = 0.75 * 100; "saadhe sau" is invalid
                if pending == 0.5:
                    return None
                current += 1.0 + pending if pending > 0 else 1.0 + pending
                pending = None
            elif current == 0:
                current = 1.0
            if val == 100:
                current *= 100
            else:
                total += current * val
                current = 0.0
        last = kind
    if pending is not None:
        return None
    return total + current


_TOKEN = re.compile(r"[^\s]+")
_EDGE = "।,.;:!?\"'()[]"


def words_to_digits(text: str) -> str:
    """Replace spoken Hindi amounts in `text` with digits. Everything else is left untouched."""
    spans = [(m.start(), m.end(), m.group().strip(_EDGE)) for m in _TOKEN.finditer(text)]
    out, i, cursor = [], 0, 0
    while i < len(spans):
        cls = _classify(spans[i][2]) if spans[i][2] else None
        if cls is None:
            i += 1
            continue
        # grow a run of consecutive number tokens (stop when two units touch: "दो तीन" is not 5)
        run, j = [], i
        while j < len(spans):
            c = _classify(spans[j][2]) if spans[j][2] else None
            if c is None:
                break
            if run and c[0] == "unit" and run[-1][0] == "unit":
                break
            run.append(c)
            j += 1
        value = _evaluate(run)
        nxt = spans[j][2] if j < len(spans) else ""
        has_scale = any(k in ("mult", "fixed", "prefix") for k, _ in run)
        followed_by_unit = (dkey(nxt) in _UNIT_WORDS_K or rkey(nxt) in _UNIT_WORDS_K) if nxt else False
        if value is not None and (has_scale or value >= 10 or followed_by_unit) and not (
                len(run) == 1 and run[0][0] == "unit" and run[0][1] < 10 and not followed_by_unit):
            out.append((spans[i][0], spans[j - 1][1], value))
            i = j
        else:
            i += 1
    result = []
    for start, end, value in out:
        result.append(text[cursor:start])
        result.append(str(int(value)) if float(value).is_integer() else str(value))
        cursor = end
    result.append(text[cursor:])
    return "".join(result)


def normalize_digits(text: str) -> str:
    """Convert digits of any script (Devanagari, Bengali, Tamil ...) to ASCII 0-9."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() else ch for ch in text)
