"""The evaluation harness. These prove the SCORER is right; they say nothing about any model, because no
model is called here."""
import pytest

from app import evaluation
from app.llm.base import ToolCall
from app.text import extract_numbers, normalize

CASES = evaluation.load_cases()


def test_the_case_set():
    langs = {c["lang"] for c in CASES}
    assert len(CASES) >= 50 and {"Hindi", "Hinglish", "English", "Tamil"} <= langs
    assert {c["noise"] for c in CASES} == {"clean", "noisy"}
    assert len({c["id"] for c in CASES}) == len(CASES)                    # ids are unique


@pytest.mark.parametrize("case", [c for c in CASES if not c.get("known_gap")], ids=lambda c: c["id"])
def test_every_expected_amount_reaches_the_model(case):
    """Integrity: a labelled amount must survive text preparation, or no model could ever score it and the
    number guard would (rightly) refuse it. Cases where that is a known weakness are marked `known_gap`."""
    present = extract_numbers(normalize(case["text"]))
    for e in case["expect"]["entries"]:
        assert f"{e['amount']:g}" in present, f"{case['id']}: {e['amount']} not in {normalize(case['text'])!r}"


def test_known_gaps_really_are_gaps():
    for case in [c for c in CASES if c.get("known_gap")]:
        present = extract_numbers(normalize(case["text"]))
        assert any(f"{e['amount']:g}" not in present for e in case["expect"]["entries"]), case["id"]


def test_a_perfect_model_scores_full_marks():
    report = evaluation.build_report(evaluation.run_all(CASES, evaluation.OracleProvider()))
    s = report.by["overall"]["all"]
    assert s.cases == len(CASES) - len(report.gaps) and report.failures == []
    assert s.amount == s.type == s.category == s.platform == s.entries and s.complete == s.log_cases
    assert s.intent == s.cases and report.clean_amount_accuracy() == 1.0


class FlawedOracle(evaluation.OracleProvider):
    """Swaps income and expense, and invents a platform: the scorer must notice both."""

    def choose_actions(self, system, tools, user, max_tokens=1024):
        calls = super().choose_actions(system, tools, user, max_tokens)
        for c in calls:
            for e in c.args.get("entries", []):
                if e.get("type") in ("income", "expense"):
                    e["type"] = "expense" if e["type"] == "income" else "income"
                    e["category"] = "fuel" if e["type"] == "expense" else "earnings"
        return calls


def test_the_scorer_catches_a_wrong_type_and_explains_it():
    report = evaluation.build_report(evaluation.run_all(CASES, FlawedOracle()))
    s = report.by["overall"]["all"]
    assert s.amount == s.entries                                            # amounts still right
    assert s.type < s.entries                                               # types caught as wrong
    causes = [c for _, cs in report.failures for c in cs]
    assert any(c.startswith("type for") for c in causes)


def test_a_missing_model_is_named_as_the_cause():
    run = evaluation.run_case(CASES[0])                                     # BRAIN=none in tests
    _, causes = evaluation.score_run(run)
    assert any("no model is configured" in c for c in causes)


def test_the_report_renders_and_states_the_target():
    report = evaluation.build_report(evaluation.run_all(CASES[:6], evaluation.OracleProvider()))
    text = evaluation.render(report, 95, oracle=True)
    assert "ORACLE RUN" in text and "says NOTHING about any real model" in text and "-> PASS" in text
    assert evaluation.to_json(report)["clean_amount_accuracy"] == 1.0
