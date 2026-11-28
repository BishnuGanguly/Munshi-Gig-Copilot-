"""Scores the whole pipeline against labelled transcripts.

Each case is a transcript plus the entries a correct system proposes BEFORE the rider confirms. The pipeline
runs exactly as in production (digits -> the model picks an action -> our code runs it); we read the entries it
proposes and compare. Metrics, all per expected entry unless stated:

    amount        the amount was found
    type          ... and the type (income / expense / saving, or None when the rider did not say) is right
    category      ... and the category is right
    platform      ... and the platform is right (None counts: inventing one is wrong)
    completeness  per CASE: every expected entry found and nothing extra
    intent        per CASE: the message was understood as the right kind of thing

A missed entry counts against all four entry metrics, so they are strict. Cases flagged `known_gap` are
reported but not counted toward the target.

These numbers mean something only with a LIVE model. With the oracle (a stand-in that answers from the labels)
they prove the harness works, and nothing about any model.
"""
import json
import logging
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from app import pipeline
from app.core import brain, db
from app.core.actions import REGISTRY
from app.errors import ServiceUnavailableError
from app.llm.base import ToolCall
from app.text import extract_numbers, normalize

CASES_PATH = Path(__file__).resolve().parent.parent / "tests" / "eval" / "cases.json"
AUDIO_EXTENSIONS = (".ogg", ".opus", ".wav", ".mp3", ".m4a")


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


# ------------------------------------------------------------------ running
@dataclass
class Run:
    case: dict
    entries: list = field(default_factory=list)
    intent: str = "unknown"
    warnings: list = field(default_factory=list)
    normalized: str = ""
    error: str | None = None
    total_ms: int = 0
    stages: dict = field(default_factory=dict)
    stt: str = "transcript"                 # what produced the text: a speech-to-text provider, or none


class _StageTimer(logging.Handler):
    """Collects the pipeline's own per-stage latency log lines (`stage=%s latency_ms=%d`)."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.stages: dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.args, tuple) and len(record.args) == 2 and "latency_ms" in record.msg:
            self.stages[str(record.args[0])] = int(record.args[1])


def audio_for(case: dict, audio_dir: Path | None) -> Path | None:
    if audio_dir:
        for ext in AUDIO_EXTENSIONS:
            candidate = Path(audio_dir) / f"{case['id']}{ext}"
            if candidate.exists():
                return candidate
    return None


def run_case(case: dict, audio_dir: Path | None = None, stt_label: str = "stt") -> Run:
    run = Run(case)
    timer = _StageTimer()
    log = logging.getLogger("munshi.pipeline")
    log.addHandler(timer)
    previous_level = log.level
    log.setLevel(logging.INFO)
    conn = db.connect(":memory:")
    started = time.perf_counter()
    try:
        clip = audio_for(case, audio_dir)
        if clip:
            result = pipeline.process(conn, "eval", audio=clip.read_bytes(), filename=clip.name, voice="never")
            run.stt = stt_label
        else:
            result = pipeline.process(conn, "eval", text=case["text"], voice="never")
        run.entries, run.intent = result.entries, result.intent
        run.warnings, run.normalized = result.warnings, result.normalized_text
    except (ValueError, ServiceUnavailableError) as exc:
        run.error = f"{type(exc).__name__}: {exc}"
    finally:
        log.removeHandler(timer)
        log.setLevel(previous_level)
        conn.close()
    run.total_ms = int((time.perf_counter() - started) * 1000)
    run.stages = timer.stages
    return run


# ------------------------------------------------------------------ the oracle (proves the harness, not a model)
class OracleProvider:
    """Answers from the labels. With it the score must be 100%; if it is not, the harness is broken."""
    name = "oracle"

    def __init__(self) -> None:
        self.case: dict | None = None

    def choose_actions(self, system, tools, user, max_tokens=1024):
        expect = self.case["expect"]
        if expect["entries"]:
            entries = []
            for e in expect["entries"]:
                entry = {"amount": e["amount"], "category": e["category"]}
                if e["type"]:
                    entry["type"] = e["type"]
                if e["platform"]:
                    entry["platform"] = e["platform"]
                entries.append(entry)
            return [ToolCall("log_entries", {"entries": entries})]
        if expect["intent"] == "query":
            return [ToolCall("query_summary", dict(expect["args"]))]
        if expect["intent"] == "analyze":
            return [ToolCall("analyze", dict(expect["args"]))]
        return [ToolCall("help", {})]

    def complete(self, system, user, max_tokens=512):
        if "The assistant's English reply:" not in user:
            return ""         # an insight or explanation from facts: decline, so our fixed sentence is used
        english = user.split("The assistant's English reply:", 1)[-1].strip()
        return json.dumps({"language": "en-IN", "reply": english})


def run_all(cases: list[dict], oracle: OracleProvider | None = None, audio_dir: Path | None = None,
            stt_label: str = "stt") -> list[Run]:
    if oracle is None:
        return [run_case(c, audio_dir, stt_label) for c in cases]
    runs = []
    with mock.patch.object(brain, "get_understander", lambda: brain.Understander(REGISTRY, oracle)), \
         mock.patch.object(brain, "get_renderer", lambda: brain.Renderer(oracle)):
        for c in cases:
            oracle.case = c
            runs.append(run_case(c, audio_dir, stt_label))
    return runs


# ------------------------------------------------------------------ scoring
@dataclass
class Score:
    entries: int = 0
    amount: int = 0
    type: int = 0
    category: int = 0
    platform: int = 0
    log_cases: int = 0
    complete: int = 0
    cases: int = 0
    intent: int = 0

    def add(self, other: "Score") -> None:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))


def match_entries(expected: list[dict], got: list[dict]) -> list[tuple[dict, dict | None]]:
    """Pair each expected entry with an unused proposed one of the same amount."""
    unused = list(got)
    pairs = []
    for exp in expected:
        found = next((g for g in unused if g.get("amount") == exp["amount"]), None)
        if found is not None:
            unused.remove(found)
        pairs.append((exp, found))
    return pairs


def score_run(run: Run) -> tuple[Score, list[str]]:
    """Returns (score, causes). `causes` is empty when the case passed completely."""
    expect, s, causes = run.case["expect"], Score(cases=1), []
    s.intent = int(run.intent == expect["intent"] and run.error is None)
    if run.error:
        return s, [f"the pipeline raised: {run.error}"]
    if run.intent != expect["intent"]:
        causes.append(_intent_cause(run, expect["intent"]))
    if not expect["entries"]:
        return s, causes

    pairs = match_entries(expect["entries"], run.entries)
    s.log_cases, s.entries = 1, len(pairs)
    present = set(extract_numbers(run.normalized))
    for exp, got in pairs:
        if got is None:
            s_amount_cause = (f"amount {exp['amount']:g} was not proposed"
                              + ("" if f"{exp['amount']:g}" in present else
                                 f" and is not in the text the model read ({run.normalized!r}): a number-word "
                                 "conversion gap, so the grounding guard refused it"))
            causes.append(s_amount_cause)
            continue
        s.amount += 1
        for metric in ("type", "category", "platform"):
            if got.get(metric) == exp[metric]:
                setattr(s, metric, getattr(s, metric) + 1)
            else:
                causes.append(f"{metric} for {exp['amount']:g}: got {got.get(metric)!r}, expected {exp[metric]!r}")
    extra = len(run.entries) - sum(1 for _, g in pairs if g is not None)
    if extra > 0:
        causes.append(f"{extra} extra entry/entries proposed that the rider did not say")
    if all(g is not None for _, g in pairs) and extra == 0:
        s.complete = 1
    elif not any("amount" in c and "not proposed" in c for c in causes):
        causes.append("incomplete")
    return s, causes


def _intent_cause(run: Run, expected: str) -> str:
    if "understanding_unavailable" in run.warnings:
        return "no model is configured (BRAIN=none): nothing could be understood"
    if run.intent == "unknown":
        return (f"understood as 'unknown' (expected {expected!r}): the model returned no usable action - it "
                "failed, refused, or chose something that failed validation or the number guard")
    return f"understood as {run.intent!r}, expected {expected!r}"


def percent(n: int, d: int) -> str:
    return f"{100 * n / d:5.1f}%" if d else "    - "


@dataclass
class Report:
    runs: list[Run]
    by: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(Score)))
    failures: list = field(default_factory=list)       # (run, causes)
    gaps: list = field(default_factory=list)           # (run, causes) for known_gap cases

    def clean_amount_accuracy(self) -> float | None:
        s = self.by["noise"].get("clean")
        return s.amount / s.entries if s and s.entries else None


def build_report(runs: list[Run]) -> Report:
    report = Report(runs)
    for run in runs:
        score, causes = score_run(run)
        if run.case.get("known_gap"):
            report.gaps.append((run, causes))
            continue
        for dim, key in (("overall", "all"), ("lang", run.case["lang"]), ("noise", run.case["noise"]),
                         ("stt", run.stt)):
            report.by[dim][key].add(score)
        if causes:
            report.failures.append((run, causes))
    return report


# ------------------------------------------------------------------ printing
def _table(title: str, rows: dict) -> list[str]:
    out = [f"\n{title}", f"  {'':16}{'cases':>6}{'entries':>9}{'amount':>9}{'type':>8}{'category':>10}"
                        f"{'platform':>10}{'complete':>10}{'intent':>8}"]
    for name, s in rows.items():
        out.append(f"  {name:16}{s.cases:>6}{s.entries:>9}{percent(s.amount, s.entries):>9}{percent(s.type, s.entries):>8}"
                   f"{percent(s.category, s.entries):>10}{percent(s.platform, s.entries):>10}"
                   f"{percent(s.complete, s.log_cases):>10}{percent(s.intent, s.cases):>8}")
    return out


def _latency(runs: list[Run]) -> list[str]:
    stages: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        for name, ms in r.stages.items():
            stages[name].append(ms)
        stages["TOTAL"].append(r.total_ms)
    out = ["\nLatency per message (ms)", f"  {'stage':16}{'mean':>8}{'median':>8}{'max':>8}"]
    for name, values in stages.items():
        out.append(f"  {name:16}{statistics.mean(values):>8.0f}{statistics.median(values):>8.0f}{max(values):>8}")
    return out


def render(report: Report, target: float = 95.0, oracle: bool = False) -> str:
    out = []
    if oracle:
        out += ["*** ORACLE RUN: the model is a stand-in that answers from the labels. This proves the harness",
                "*** works. It says NOTHING about any real model. ***"]
    out += _table("By language", report.by["lang"])
    out += _table("Clean vs noisy transcripts", report.by["noise"])
    out += _table("By speech-to-text provider", report.by["stt"])
    out += _table("Overall", report.by["overall"])
    out += _latency(report.runs)
    if report.gaps:
        out.append("\nKnown gaps (reported, not counted toward the target)")
        for run, _ in report.gaps:
            out.append(f"  [{run.case['id']}] {run.case['text']!r}\n      {run.case['known_gap']}")
    if report.failures:
        out.append(f"\nFailures ({len(report.failures)} of {len(report.runs) - len(report.gaps)} counted cases)")
        for run, causes in report.failures:
            out.append(f"  [{run.case['id']} {run.case['lang']}/{run.case['noise']}] {run.case['text']!r}")
            out += [f"      - {c}" for c in causes]
    else:
        out.append("\nNo failures.")
    acc = report.clean_amount_accuracy()
    if acc is None:
        out.append("\nTarget: no clean entries were scored.")
    else:
        verdict = "PASS" if acc * 100 >= target else "FAIL"
        out.append(f"\nTarget: amount accuracy on clean transcripts {acc * 100:.1f}% (need {target:g}%)  -> {verdict}")
    return "\n".join(out)


def to_json(report: Report) -> dict:
    def dump(s: Score) -> dict:
        return {k: getattr(s, k) for k in s.__dataclass_fields__}
    return {"by": {dim: {k: dump(v) for k, v in rows.items()} for dim, rows in report.by.items()},
            "failures": [{"id": r.case["id"], "causes": c} for r, c in report.failures],
            "known_gaps": [r.case["id"] for r, _ in report.gaps],
            "clean_amount_accuracy": report.clean_amount_accuracy()}
