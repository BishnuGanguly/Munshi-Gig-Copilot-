"""A fake provider that speaks our own interface (app/llm/base.py), so tests of the brain do not depend on
any vendor's wire format. Vendor shapes are tested separately, in tests/test_llm_*.py."""
import json

from app.llm.base import ToolCall


class FakeProvider:
    """`turns` is a list of tool-call lists, one per `choose_actions` call; the last one repeats.
    `text` is what `complete` returns. Everything asked of it is recorded in `requests`."""
    name = "fake"

    def __init__(self, turns=(), text="", raises=None):
        self.turns, self.text, self.raises = [list(t) for t in turns], text, raises
        self.requests = []

    def choose_actions(self, system, tools, user, max_tokens=1024):
        self.requests.append({"kind": "choose", "system": system, "tools": tools, "user": user})
        if self.raises:
            raise self.raises
        n = len([r for r in self.requests if r["kind"] == "choose"])
        calls = self.turns[min(n - 1, len(self.turns) - 1)] if self.turns else []
        return [ToolCall(name, args) for name, args in calls]

    def complete(self, system, user, max_tokens=512):
        self.requests.append({"kind": "complete", "system": system, "user": user})
        if self.raises:
            raise self.raises
        return self.text

    @property
    def chooses(self):
        return [r for r in self.requests if r["kind"] == "choose"]

    @property
    def completes(self):
        return [r for r in self.requests if r["kind"] == "complete"]


def rendering(language, reply):
    """What a well-behaved model returns from the renderer."""
    return json.dumps({"language": language, "reply": reply})
