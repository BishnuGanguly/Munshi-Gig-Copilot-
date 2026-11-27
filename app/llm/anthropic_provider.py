"""Claude via the official Anthropic SDK. Optional: `uv sync --extra llm`.

Authenticates the way the SDK does - ANTHROPIC_API_KEY, or the OAuth profile from `ant auth login`.
Uses prompt caching on the static system prompt and low effort (a router wants speed over deliberation).
"""
import logging

from app.errors import LlmUnavailableError
from .base import ToolCall

log = logging.getLogger("munshi.llm.anthropic")
DEFAULT_MODEL = "claude-haiku-5-5"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client=None, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise LlmUnavailableError("The `anthropic` package is not installed (uv sync --extra llm).") from exc
            self._client = anthropic.Anthropic()
        return self._client

    @staticmethod
    def _system(text: str) -> list[dict]:
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]   # static prefix caches

    def _create(self, **kw):
        try:
            return self._get_client().messages.create(model=self.model, output_config={"effort": "low"}, **kw)
        except LlmUnavailableError:
            raise
        except Exception as exc:                  # network, auth, rate limit: all mean "unavailable"
            raise LlmUnavailableError(f"Anthropic call failed: {type(exc).__name__}: {exc}") from exc

    def choose_actions(self, system: str, tools: list[dict], user: str, max_tokens: int = 4096) -> list[ToolCall]:
        response = self._create(max_tokens=max_tokens, system=self._system(system), tools=tools,
                                tool_choice={"type": "auto"}, messages=[{"role": "user", "content": user}])
        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("anthropic refused: %s", getattr(response, "stop_details", None))
            return []
        calls = [ToolCall(b.name, b.input) for b in response.content if getattr(b, "type", "") == "tool_use"]
        log.info("anthropic model=%s calls=%s cache_read=%s", self.model, [c.name for c in calls],
                 getattr(getattr(response, "usage", None), "cache_read_input_tokens", None))
        return calls

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        response = self._create(max_tokens=max_tokens, system=self._system(system),
                                messages=[{"role": "user", "content": user}])
        if getattr(response, "stop_reason", None) == "refusal":
            return ""
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
