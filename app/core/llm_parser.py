"""Optional LLM fallback for messages the rules can't parse. OFF by default (PARSER_LLM=none).

Enable with PARSER_LLM=anthropic, ANTHROPIC_API_KEY=... and `uv sync --extra llm`. Model: LLM_PARSE_MODEL
(default claude-haiku-5-5). The model only EXTRACTS entries as JSON; every value is validated in code and
nothing is saved without the user's confirmation, so a wrong guess is caught by the read-back."""
import json
import logging
import os
import re
from datetime import date, timedelta

from . import vocab
from .parser import validate_entry

log = logging.getLogger("munshi.llm_parser")

_SYSTEM = (
    "You extract money entries from one short English message written by an Indian delivery or ride-hailing rider. "
    'Reply with JSON only, no other text: {"entries": [{"type": "income|expense|saving", "amount": number, '
    '"category": "earnings|incentive|fuel|repair|recharge|emi|rent|food|penalty|other|savings", '
    '"platform": "Swiggy|Zomato|Uber|Ola|Rapido|Blinkit|Zepto|Porter|Dunzo|null", '
    '"day": "today|yesterday|day_before"}]}. Use an empty list when the message has no money amount. '
    "Never invent amounts. If unsure whether it is income or expense, choose the most likely one."
)
_DAYS = {"today": 0, "yesterday": -1, "day_before": -2}
_ALL_CATEGORIES = set(vocab.CATEGORIES) | set(vocab.INCOME_CATEGORIES) | {"savings"}


class LlmLogParser:
    def __init__(self, client=None, model: str | None = None) -> None:
        self.model = model or os.getenv("LLM_PARSE_MODEL", "claude-haiku-5-5")
        self._client = client

    def _get_client(self):
        if self._client is None:
            import anthropic  # ImportError is handled by the caller
            self._client = anthropic.Anthropic()
        return self._client

    def parse(self, text: str, today: date) -> list[dict]:
        """Return validated entries, or [] on any failure (the caller then asks the user to rephrase)."""
        try:
            resp = self._get_client().messages.create(
                model=self.model, max_tokens=512, system=_SYSTEM, messages=[{"role": "user", "content": text}])
            if getattr(resp, "stop_reason", None) == "refusal":
                return []
            raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return self._clean(raw, today)
        except Exception as exc:  # network, auth, missing package: never crash the conversation
            log.warning("LLM parse failed: %s: %s", type(exc).__name__, exc)
            return []

    @staticmethod
    def _clean(raw: str, today: date) -> list[dict]:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return []
        try:
            items = json.loads(m.group(0)).get("entries", [])
        except (ValueError, AttributeError):
            return []
        entries = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            try:
                amount = float(it.get("amount"))
            except (TypeError, ValueError):
                continue
            etype = it.get("type")
            category = it.get("category")
            if category not in _ALL_CATEGORIES:
                category = {"income": "earnings", "saving": "savings"}.get(etype, "other")
            platform = it.get("platform")
            platform = vocab.canonical_platform(platform) if isinstance(platform, str) else None
            offset = _DAYS.get(it.get("day"), 0)
            entry = {"type": etype, "amount": amount, "category": category, "platform": platform,
                     "date": (today + timedelta(days=offset)).isoformat()}
            if not validate_entry(entry):
                entries.append(entry)
        return entries


def get_llm_parser() -> LlmLogParser | None:
    return LlmLogParser() if os.getenv("PARSER_LLM", "none") == "anthropic" else None
