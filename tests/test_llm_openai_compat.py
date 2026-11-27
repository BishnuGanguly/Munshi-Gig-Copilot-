"""The OpenAI-style adapter (Sarvam, OpenAI, Groq, OpenRouter, Ollama ...), against a mocked HTTP server."""
import json

import httpx
import pytest

from app.core.actions import REGISTRY
from app.errors import LlmUnavailableError
from app.llm.openai_compat import (OpenAICompatProvider, simplify_schema, strip_reasoning, to_openai_tools)


def provider(handler, **kw):
    kw.setdefault("auth", "header:api-subscription-key")
    return OpenAICompatProvider("sarvam", "https://api.sarvam.ai/v1", "sarvam-105b", api_key="k", retries=kw.pop("retries", 1),
                                transport=httpx.MockTransport(handler), **kw)


def reply(message, status=200):
    return httpx.Response(status, json={"choices": [{"message": message, "finish_reason": "stop"}]})


def tool_call(name, arguments):
    return {"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}


TOOLS = REGISTRY.tool_definitions()


# ---------------------------------------------------------------- the request we send
def test_request_shape():
    seen = {}

    def handler(request):
        seen["url"], seen["headers"], seen["body"] = str(request.url), request.headers, json.loads(request.content)
        return reply({"content": None, "tool_calls": [tool_call("help", "{}")]})

    provider(handler).choose_actions("SYSTEM", TOOLS, "the rider says hi", max_tokens=300)
    assert seen["url"] == "https://api.sarvam.ai/v1/chat/completions"
    assert seen["headers"]["api-subscription-key"] == "k" and "authorization" not in seen["headers"]
    b = seen["body"]
    assert b["model"] == "sarvam-105b" and b["tool_choice"] == "auto" and b["max_tokens"] == 300
    assert b["messages"] == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "the rider says hi"}]
    assert [t["function"]["name"] for t in b["tools"]] == REGISTRY.names()
    assert all(t["type"] == "function" and set(t["function"]) == {"name", "description", "parameters"} for t in b["tools"])


@pytest.mark.parametrize("auth,expected", [
    ("bearer", {"authorization": "Bearer k"}), ("header:X-Api-Key", {"x-api-key": "k"}), ("none", {})])
def test_auth_styles(auth, expected):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return reply({"content": "hi"})

    OpenAICompatProvider("p", "http://h/v1", "m", api_key="k", auth=auth, transport=httpx.MockTransport(handler)
                         ).complete("s", "u")
    assert all(seen.get(k) == v for k, v in expected.items())
    if auth == "none":
        assert "authorization" not in seen and "x-api-key" not in seen


def test_base_url_trailing_slash_is_tolerated():
    p = OpenAICompatProvider("p", "http://localhost:11434/v1/", "m", auth="none")
    assert p.url == "http://localhost:11434/v1/chat/completions"


# ---------------------------------------------------------------- reading tool calls
def test_arguments_arrive_as_a_json_string():
    calls = provider(lambda r: reply({"content": None, "tool_calls": [
        tool_call("log_entries", json.dumps({"entries": [{"type": "income", "amount": 1400, "platform": "Swiggy"}]}))]})
    ).choose_actions("s", TOOLS, "u")
    assert [(c.name, c.args["entries"][0]["amount"]) for c in calls] == [("log_entries", 1400)]


@pytest.mark.parametrize("raw,expected", [("", {}), (None, {}), ("{}", {}), ({"amount": 5}, {"amount": 5})])
def test_empty_or_pre_decoded_arguments(raw, expected):
    calls = provider(lambda r: reply({"tool_calls": [tool_call("set_target", raw)]})).choose_actions("s", TOOLS, "u")
    assert calls[0].args == expected


def test_malformed_arguments_are_no_call_not_a_crash():
    calls = provider(lambda r: reply({"tool_calls": [
        tool_call("log_entries", '{"entries": [{"amount": 14'),         # cut off mid-JSON
        tool_call("help", "{}")]})).choose_actions("s", TOOLS, "u")
    assert [c.name for c in calls] == ["help"]                            # the good call survives


def test_several_parallel_calls_are_all_returned():
    """Parallel tool calls cannot be switched off on every model, so the caller caps them."""
    calls = provider(lambda r: reply({"tool_calls": [tool_call("help", "{}")] * 4})).choose_actions("s", TOOLS, "u")
    assert len(calls) == 4


@pytest.mark.parametrize("message", [{"content": "I think you want help"}, {"content": None}, {}, None])
def test_no_tool_call_is_an_empty_list(message):
    assert provider(lambda r: reply(message)).choose_actions("s", TOOLS, "u") == []


def test_a_tool_call_without_a_name_is_skipped():
    assert provider(lambda r: reply({"tool_calls": [{"function": {"arguments": "{}"}}, None]})
                    ).choose_actions("s", TOOLS, "u") == []


# ---------------------------------------------------------------- reading text
@pytest.mark.parametrize("content,expected", [
    ("plain", "plain"),
    ("<think>let me think</think>the answer", "the answer"),
    ("<THINK>x\nmulti\nline</THINK>\n\nanswer", "answer"),
    ("<think>cut off while thinking", ""),
    ([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "ab"),
    (None, ""),
])
def test_complete_reads_the_reply(content, expected):
    assert provider(lambda r: reply({"content": content})).complete("s", "u") == expected


def test_strip_reasoning_helper():
    assert strip_reasoning("<think>a</think>b<think>c</think>d") == "bd"


# ---------------------------------------------------------------- failures are one error type
@pytest.mark.parametrize("status,match", [(401, "API key"), (403, "API key"), (400, "rejected the request"),
                                          (404, "rejected the request"), (422, "rejected the request")])
def test_client_errors_do_not_retry(status, match):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status, text="no")

    with pytest.raises(LlmUnavailableError, match=match):
        provider(handler, retries=3).complete("s", "u")
    assert calls["n"] == 1


def test_server_errors_and_timeouts_retry_then_fail(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(503, text="busy")

    with pytest.raises(LlmUnavailableError, match="failed after 3 attempts"):
        provider(handler, retries=2).complete("s", "u")
    assert calls["n"] == 3


def test_retry_succeeds_after_a_transient_failure(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] == 1 else reply({"content": "ok"})

    assert provider(handler).complete("s", "u") == "ok"


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"), httpx.Response(200, json={}), httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={"choices": [{}]}), httpx.Response(200, json={"choices": "x"})])
def test_unexpected_responses_are_unavailable_not_a_crash(response):
    with pytest.raises(LlmUnavailableError):
        provider(lambda r: response).choose_actions("s", TOOLS, "u")


def test_missing_key_and_bad_auth_style():
    with pytest.raises(LlmUnavailableError, match="no API key"):
        OpenAICompatProvider("p", "http://h/v1", "m", api_key="", transport=httpx.MockTransport(lambda r: reply({}))
                             ).complete("s", "u")
    with pytest.raises(LlmUnavailableError, match="unknown auth style"):
        OpenAICompatProvider("p", "http://h/v1", "m", api_key="k", auth="magic",
                             transport=httpx.MockTransport(lambda r: reply({}))).complete("s", "u")


# ---------------------------------------------------------------- schema compatibility
def test_nullable_fields_are_simplified_for_stricter_providers():
    schema = {"type": "object", "properties": {
        "platform": {"type": ["string", "null"], "enum": ["Swiggy", "Zomato", None]},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "items": {"type": "array", "items": {"type": "object", "properties": {"x": {"type": ["number", "null"]}}}}}}
    out = simplify_schema(schema)
    assert out["properties"]["platform"] == {"type": "string", "enum": ["Swiggy", "Zomato"]}
    assert out["properties"]["kind"] == {"type": "string", "enum": ["a", "b"]}
    assert out["properties"]["items"]["items"]["properties"]["x"] == {"type": "number"}
    assert schema["properties"]["platform"]["type"] == ["string", "null"]         # the original is untouched


def test_no_advertised_schema_contains_null():
    """Every action's schema, as advertised, is free of the constructs strict providers choke on."""
    text = json.dumps(to_openai_tools(TOOLS))
    assert "null" not in text and "None" not in text


def test_registry_still_validates_against_the_original_schema():
    """Advertising a simpler schema must not weaken validation: a stray null is still accepted by ours."""
    from app.core.registry import validate_args
    entry_schema = REGISTRY.get("log_entries").input_schema
    assert validate_args(entry_schema, {"entries": [{"amount": 5, "platform": None}]}) == []


# ---------------------------------------------------------------- reasoning models (found live: sarvam-105b)
def body_of(handler_capture):
    return handler_capture[-1]


def capturing(response):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return response() if callable(response) else response

    return handler, seen


def thinking_model(**kw):
    return OpenAICompatProvider("sarvam", "https://api.sarvam.ai/v1", "sarvam-105b", api_key="k",
                                auth="header:api-subscription-key", retries=0, **kw)


def test_the_exact_live_failure_is_now_explained_not_silent():
    """Reproduces the first live run: the reasoning spent the whole budget, so `content` came back empty and
    finish_reason was `length`. It used to look like a model that said nothing; now it names the cause."""
    body = {"choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": "Let me think..."},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 430, "completion_tokens": 512}}
    p = OpenAICompatProvider("sarvam", "https://api.sarvam.ai/v1", "sarvam-105b", api_key="k", retries=0,
                             auth="header:api-subscription-key",
                             transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(LlmUnavailableError, match="token limit.*reasoning model.*BRAIN_REASONING_SARVAM"):
        p.complete("s", "u")
    assert p.last_finish == "length" and p.last_usage["completion_tokens"] == 512


def test_running_dry_while_choosing_is_not_the_same_as_choosing_nothing():
    """A truncated reply is a failure (a fallback may do better); a model that simply chose no action is not."""
    dry = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
    quiet = {"choices": [{"message": {"content": "no tool needed"}, "finish_reason": "stop"}]}
    with pytest.raises(LlmUnavailableError, match="token limit"):
        provider(lambda r: httpx.Response(200, json=dry)).choose_actions("s", TOOLS, "u")
    assert provider(lambda r: httpx.Response(200, json=quiet)).choose_actions("s", TOOLS, "u") == []


def test_a_truncated_reply_that_still_has_text_is_kept():
    body = {"choices": [{"message": {"content": "partial but usable"}, "finish_reason": "length"}]}
    assert provider(lambda r: httpx.Response(200, json=body)).complete("s", "u") == "partial but usable"


def test_the_budgets_leave_room_to_think_and_answer():
    handler, seen = capturing(lambda: reply({"content": "x", "tool_calls": [tool_call("help", "{}")]}))
    p = provider(handler)
    p.choose_actions("s", TOOLS, "u")
    p.complete("s", "u")
    assert seen[0]["max_tokens"] == 4096 and seen[1]["max_tokens"] == 2048     # the old 1024 / 512 ran dry


def test_reasoning_effort_is_sent_per_kind_of_call():
    handler, seen = capturing(lambda: reply({"content": "x", "tool_calls": [tool_call("help", "{}")]}))
    p = thinking_model(transport=httpx.MockTransport(handler), reasoning={"choose": "low", "complete": "off"})
    p.choose_actions("s", TOOLS, "u")
    p.complete("s", "u")
    assert seen[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" in seen[1] and seen[1]["reasoning_effort"] is None      # JSON null = thinking off


@pytest.mark.parametrize("setting", [None, "omit", ""])
def test_by_default_nothing_extra_is_sent(setting):
    """Providers that do not know the field must be unaffected."""
    handler, seen = capturing(reply({"content": "x"}))
    p = thinking_model(transport=httpx.MockTransport(handler), reasoning={"complete": setting} if setting is not None else {})
    p.complete("s", "u")
    assert "reasoning_effort" not in seen[0]


def test_a_provider_that_rejects_the_field_is_retried_without_it_and_remembered():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(422, text="unknown field reasoning_effort")
        return reply({"content": "fine"})

    p = thinking_model(transport=httpx.MockTransport(handler), reasoning={"complete": "off"})
    assert p.complete("s", "u") == "fine"
    assert "reasoning_effort" in calls[0] and "reasoning_effort" not in calls[1]
    assert p.complete("s", "u") == "fine"
    assert "reasoning_effort" not in calls[2]                                  # not sent again


def test_a_genuine_400_without_the_field_is_still_an_error():
    handler, _ = capturing(httpx.Response(400, text="bad tool schema"))
    with pytest.raises(LlmUnavailableError, match="rejected the request"):
        provider(handler).complete("s", "u")


def test_usage_is_recorded_for_diagnosis():
    body = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 40, "reasoning_tokens": 12}}
    p = provider(lambda r: httpx.Response(200, json=body))
    p.complete("s", "u")
    assert p.last_finish == "stop" and p.last_usage == {"completion_tokens": 40, "reasoning_tokens": 12}


def test_the_whole_reply_is_kept_so_a_failure_can_be_read():
    body = {"choices": [{"message": {"content": "", "reasoning_content": "hmm"}, "finish_reason": "stop"}], "id": "x1"}
    p = provider(lambda r: httpx.Response(200, json=body))
    p.complete("s", "u")
    assert p.last_response == body
