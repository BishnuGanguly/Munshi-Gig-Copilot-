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
