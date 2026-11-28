"""Smoke-test each configured model provider with three real calls: does it pick the right action with
the right arguments, can it reword a reply without changing a number, and after a save does it choose an
insight from our computed facts without inventing a number?

    uv run python scripts/check_llm.py                    # every provider named in BRAIN
    uv run python scripts/check_llm.py --provider sarvam  # just one

This is the first thing to run once you have a key. It exists because the provider code is tested against
mocked servers only: the endpoint, the auth header and the shape of a tool call were taken from docs seen
through search results, never exercised. A failure here names which of those is wrong.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402  (loads .env)
from app.core import brain  # noqa: E402
from app.core.actions import REGISTRY  # noqa: E402
from app.errors import LlmUnavailableError  # noqa: E402
from app.llm import KNOWN, build_provider  # noqa: E402
import os  # noqa: E402


def check(provider) -> bool:
    ok = True
    print(f"\n== {provider.name}  (model: {getattr(provider, 'model', '?')}) ==")

    message = "aaj Swiggy se 1400 aaye aur petrol 250 ka dala"
    t = time.perf_counter()
    try:
        calls = provider.choose_actions(brain.SYSTEM_UNDERSTAND % 3, REGISTRY.tool_definitions(),
                                        f"Today is 2026-11-25.\n\nThe rider says: {brain.preprocess(message)}")
    except LlmUnavailableError as exc:
        print(f"  [FAIL] choose_actions: {exc}")
        return False
    ms = int((time.perf_counter() - t) * 1000)
    print(f"  choose_actions -> {[(c.name, c.args) for c in calls]}  ({ms} ms)")
    good = [c for c in calls if c.name == "log_entries"]
    amounts = sorted(e.get("amount") for c in good for e in c.args.get("entries", []) if isinstance(e, dict))
    if amounts == [250, 1400] and not brain.ungrounded_numbers(good[0].args, brain.preprocess(message)):
        print("  [ ok ] picked log_entries with both amounts, and every number is grounded")
    else:
        print("  [FAIL] expected one log_entries call with amounts 250 and 1400")
        ok = False
    if len(calls) > 1:
        print(f"  [note] {len(calls)} calls came back; the app reads at most {brain.MAX_ACTIONS}")

    english = "Please confirm: income ₹1,400 from Swiggy; expense ₹250 on fuel. Is that correct?"
    renderer = brain.Renderer(provider)
    t = time.perf_counter()
    reply, language, warnings = renderer.render(english, message)
    ms = int((time.perf_counter() - t) * 1000)
    print(f"  render -> [{language}] {reply}  ({ms} ms)")
    if warnings:
        print(f"  [FAIL] render fell back to English: {warnings}")
        print(f"         why  : {renderer.last_problem}")
        print(f"         model said: {renderer.last_raw[:600]!r}")
        print(f"         finish_reason={getattr(provider, 'last_finish', None)} usage={getattr(provider, 'last_usage', {})}")
        raw = getattr(provider, "last_response", None)
        if raw:
            print("         the provider's whole reply:")
            print("         " + json.dumps(raw, ensure_ascii=False)[:1800])
        ok = False
    elif reply.strip() == english.strip():
        print("  [FAIL] the reply came back word for word in English: the rider wrote Hinglish, so a label of "
              f"{language!r} on unchanged English is not a rewording")
        ok = False
    elif language not in ("hi-Latn", "hi-IN"):
        print(f"  [warn] expected Hinglish for a Hinglish message, got {language!r}")
    else:
        print("  [ ok ] reworded in the rider's language, numbers intact")

    # After a save: the model chooses the insight from facts we computed.
    facts = {"saved": "income ₹350", "today_income": 1650, "today_costs": 260, "today_after_costs": 1390,
             "daily_target": 1500, "target_reached_just_now": True, "above_target_by": 150,
             "this_week_income": 6200, "biggest_cost_this_week": "fuel", "biggest_cost_this_week_amount": 980,
             "biggest_cost_share_of_week_income_percent": 16}
    fixed = "Saved. Today ₹1,650 in, ₹260 spent: ₹1,390 after costs. You reached your ₹1,500 target!"
    renderer = brain.Renderer(provider)
    t = time.perf_counter()
    reply, language, warnings = renderer.render_insight(fixed, facts, "aur 350 mile")
    ms = int((time.perf_counter() - t) * 1000)
    print(f"  insight -> [{language}] {reply}  ({ms} ms)  facts used: {renderer.last_used}")
    if warnings:
        print(f"  [FAIL] the model's insight was not used: {warnings}")
        print(f"         model said: {renderer.last_raw[:600]!r}")
        ok = False
    elif language not in ("hi-Latn", "hi-IN"):
        print(f"  [warn] expected Hinglish for a Hinglish message, got {language!r}")
    else:
        print("  [ ok ] chose an insight from the facts, every number is one of them")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", help=f"one of: {', '.join(KNOWN)} (default: whatever BRAIN names)")
    args = ap.parse_args()
    get_settings()
    names = [args.provider] if args.provider else [
        n for n in os.getenv("BRAIN", "none").replace(" ", "").lower().split(",") if n and n != "none"]
    if not names:
        print("BRAIN is not set. Set e.g. BRAIN=sarvam in .env, or pass --provider sarvam.")
        return 2
    results = {n: check(build_provider(n, primary=(i == 0))) for i, n in enumerate(names)}
    print("\n" + "\n".join(f"{'PASS' if v else 'FAIL'}  {k}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
