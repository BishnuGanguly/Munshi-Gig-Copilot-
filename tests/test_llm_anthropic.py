"""The Claude adapter, against a fake Anthropic client."""
import pytest

from app.core.actions import REGISTRY
from app.errors import LlmUnavailableError
from app.llm.anthropic_provider import AnthropicProvider

TOOLS = REGISTRY.tool_definitions()


class Block:
    def __init__(self, type_, **kw):
        self.type = type_
        self.__dict__.update(kw)


class FakeMessages:
    def __init__(self, content=(), stop_reason="end_turn", raises=None):
        self.content, self.stop_reason, self.raises = list(content), stop_reason, raises
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        if self.raises:
            raise self.raises
        return type("R", (), {"content": self.content, "stop_reason": self.stop_reason, "stop_details": None,
                              "usage": type("U", (), {"cache_read_input_tokens": 0})()})()


def provider(**kw):
    msgs = FakeMessages(**kw)
    return AnthropicProvider(client=type("C", (), {"messages": msgs})()), msgs


def test_choose_actions_request_shape():
    p, msgs = provider(content=[Block("tool_use", name="help", input={})], stop_reason="tool_use")
    calls = p.choose_actions("SYSTEM", TOOLS, "hello", max_tokens=300)
    req = msgs.requests[0]
    assert req["model"] == "claude-haiku-5-5" and req["max_tokens"] == 300
    assert req["tool_choice"] == {"type": "auto"} and req["output_config"] == {"effort": "low"}
    assert req["system"] == [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}]
    assert req["tools"] == TOOLS                                    # Anthropic takes our neutral shape as is
    assert req["messages"] == [{"role": "user", "content": "hello"}]
    assert [(c.name, c.args) for c in calls] == [("help", {})]


def test_complete_request_shape_has_no_tools():
    p, msgs = provider(content=[Block("text", text='{"a": 1}')])
    assert p.complete("SYSTEM", "u") == '{"a": 1}'
    assert "tools" not in msgs.requests[0] and "tool_choice" not in msgs.requests[0]


def test_text_blocks_alongside_tool_calls_are_ignored():
    p, _ = provider(content=[Block("text", text="thinking aloud"), Block("tool_use", name="help", input={})])
    assert [c.name for c in p.choose_actions("s", TOOLS, "u")] == ["help"]


def test_refusal_is_no_action_not_an_error():
    p, _ = provider(content=[], stop_reason="refusal")
    assert p.choose_actions("s", TOOLS, "u") == [] and p.complete("s", "u") == ""


def test_model_is_configurable():
    assert AnthropicProvider(client=object(), model="claude-sonnet-5-5").model == "claude-sonnet-5-5"


@pytest.mark.parametrize("error", [RuntimeError("connection reset"), ValueError("bad"), TimeoutError()])
def test_any_failure_is_one_error_type(error):
    p, _ = provider(raises=error)
    with pytest.raises(LlmUnavailableError):
        p.choose_actions("s", TOOLS, "u")
    with pytest.raises(LlmUnavailableError):
        p.complete("s", "u")


def test_a_missing_package_is_survivable(monkeypatch):
    import builtins
    real = builtins.__import__

    def blocked(name, *a, **kw):
        if name == "anthropic":
            raise ImportError("no anthropic")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(LlmUnavailableError, match="not installed"):
        AnthropicProvider().complete("s", "u")
