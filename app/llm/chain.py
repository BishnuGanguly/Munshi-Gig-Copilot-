"""Try providers in order. A provider that is *down* is skipped; one that simply chose no action is not,
because retrying a model's judgement on a second vendor would double the cost and hide a quality problem."""
import logging

from app.errors import LlmUnavailableError
from .base import LlmProvider, ToolCall

log = logging.getLogger("munshi.llm.chain")


class FallbackLlm:
    name = "chain"

    def __init__(self, providers: list[LlmProvider]) -> None:
        if not providers:
            raise ValueError("a provider chain needs at least one provider")
        self.providers = providers

    def _first(self, call):
        errors: list[str] = []
        for provider in self.providers:
            try:
                return call(provider)
            except LlmUnavailableError as exc:
                log.warning("llm provider %s unavailable: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
        raise LlmUnavailableError("; ".join(errors))

    def choose_actions(self, system: str, tools: list[dict], user: str, max_tokens: int = 4096) -> list[ToolCall]:
        return self._first(lambda p: p.choose_actions(system, tools, user, max_tokens))

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        return self._first(lambda p: p.complete(system, user, max_tokens))
