"""Score the whole pipeline on 51 labelled transcripts (Hindi, Hinglish, English, Tamil; clean and noisy).

    uv run python scripts/evaluate.py                 # with the model named in BRAIN: the real measurement
    uv run python scripts/evaluate.py --oracle        # a stand-in answering from the labels: proves the harness
    uv run python scripts/evaluate.py --lang Hindi    # one language
    uv run python scripts/evaluate.py --audio tests/eval/audio   # use clips <id>.ogg where they exist, so the
                                                                 # score also covers speech-to-text
    uv run python scripts/evaluate.py --json report.json

Reports amount / type / category / platform accuracy, completeness and intent, split by language, by
clean-vs-noisy and by speech-to-text provider, with each stage's latency, and prints every failure with its
likely cause. Exits non-zero when amount accuracy on clean input is below --target (default 95).
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402  (loads .env)
from app import evaluation  # noqa: E402
from app.core import brain  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oracle", action="store_true", help="answer from the labels instead of calling a model")
    ap.add_argument("--lang", help="only this language: English, Hinglish, Hindi or Tamil")
    ap.add_argument("--audio", type=Path, help="a folder of <case-id>.ogg/.wav/.mp3/.m4a clips")
    ap.add_argument("--cases", type=Path, default=evaluation.CASES_PATH)
    ap.add_argument("--target", type=float, default=95.0, help="required clean amount accuracy, percent")
    ap.add_argument("--json", type=Path, help="also write the numbers here")
    args = ap.parse_args()
    get_settings()

    cases = evaluation.load_cases(args.cases)
    if args.lang:
        cases = [c for c in cases if c["lang"].lower() == args.lang.lower()]
    if not cases:
        print("No cases selected.")
        return 2
    if not args.oracle and not brain.enabled():
        print("BRAIN is not set, so nothing can be understood and every case would fail.\n"
              "Set e.g. BRAIN=sarvam in .env (and run scripts/check_llm.py first), or use --oracle to test the harness.")
        return 2

    label = os.getenv("STT_PROVIDER", "mock")
    runs = evaluation.run_all(cases, evaluation.OracleProvider() if args.oracle else None, args.audio, label)
    report = evaluation.build_report(runs)
    print(evaluation.render(report, args.target, args.oracle))
    if args.json:
        args.json.write_text(json.dumps(evaluation.to_json(report), indent=1), encoding="utf-8")
    acc = report.clean_amount_accuracy()
    return 0 if acc is not None and acc * 100 >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())
