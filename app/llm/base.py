"""The one interface the rest of the app knows about. No vendor names, no vendor request shapes.

A provider does exactly two things:
  * `choose_actions`  - given tools, say which to call and with what arguments;
  * `complete`        - turn a prompt into text.

Everything that makes the model safe to use (schema validation, the number guards, confirm-before-save)
lives in app/core and works the same whichever provider answers.
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


class LlmProvider(Protocol):
    name: str

    def choose_actions(self, system: str, tools: list[dict], user: str, max_tokens: int = 1024) -> list[ToolCall]:
        """`tools` are neutral definitions: {"name", "description", "input_schema"}. Returns the calls the
        model chose - possibly none. Raises LlmUnavailableError when the provider itself failed."""
        ...

    def complete(self, system: str, user: str, max_tokens: int = 512) -> str:
        """Plain text out. Raises LlmUnavailableError when the provider itself failed."""
        ...
