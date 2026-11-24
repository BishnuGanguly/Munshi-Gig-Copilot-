"""Run the sample sentences through normalise + to-English with the CONFIGURED translation provider and
print a pass/fail table per language.

    uv run python scripts/eval_translation.py            # all languages
    uv run python scripts/eval_translation.py hi-IN      # one language

A sentence PASSES when every expected amount appears in the English output (the important check).
Keyword hits are reported separately as a looser sanity check.
Needs TRANSLATE_PROVIDER=sarvam (or llm) and a key in .env; with the default mock provider only English passes."""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402  (loads .env)
from app.errors import TranslateUnavailableError  # noqa: E402
from app.translate import get_language_service  # noqa: E402
from app.translate.normalize import extract_numbers  # noqa: E402


def main() -> int:
    get_settings()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    data = json.loads((ROOT / "tests" / "data" / "sentences.json").read_text(encoding="utf-8"))["sentences"]
    svc = get_language_service()
    stats = defaultdict(lambda: {"n": 0, "amount": 0, "keyword": 0, "error": 0})
    failures = []
    for item in data:
        lang = item["lang"]
        if only and lang != only:
            continue
        s = stats[lang]
        s["n"] += 1
        try:
            english, warnings = svc.to_english(svc.normalize(item["text"]), lang)
        except TranslateUnavailableError as exc:
            s["error"] += 1
            failures.append((lang, item["text"], f"ERROR {exc}"))
            continue
        have = extract_numbers(english)
        amounts_ok = all(n in have for n in item["numbers"])
        kw_ok = not item["keywords"] or any(k in english.lower() for k in item["keywords"])
        s["amount"] += amounts_ok
        s["keyword"] += kw_ok
        if not amounts_ok or not kw_ok:
            failures.append((lang, item["text"], f"-> {english!r}  amounts_ok={amounts_ok} keywords_ok={kw_ok}"))
    print(f"{'language':10} {'n':>3} {'amounts ok':>11} {'keywords ok':>12} {'errors':>7}")
    for lang, s in stats.items():
        print(f"{lang:10} {s['n']:>3} {s['amount']:>7}/{s['n']:<3} {s['keyword']:>8}/{s['n']:<3} {s['error']:>7}")
    if failures:
        print("\nFailures:")
        for lang, text, why in failures:
            print(f"  [{lang}] {text}\n      {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
