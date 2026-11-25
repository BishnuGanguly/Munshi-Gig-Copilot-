"""Any provider that speaks the OpenAI-style Chat Completions protocol.

That is a long list - Sarvam, OpenAI, Groq, OpenRouter, Together, Ollama, vLLM, LM Studio - so one adapter
with a base URL, an auth style and a model covers them all. Only the first of those has been checked against
its docs (and only through search snippets); the rest rely on the protocol being standard.

Wire shape handled here:
  request   {"model", "messages": [system, user], "max_tokens", "tools": [{"type":"function","function":{...}}],
             "tool_choice": "auto"}
  response  choices[0].message.tool_calls[].function.{name, arguments}   <- `arguments` is a JSON STRING
            choices[0].message.content                                   <- text, possibly with <think> blocks
"""
import json
import logging
import re
import time

import httpx

from app.errors import LlmUnavailableError
from .base import ToolCall

log = logging.getLogger("munshi.llm.openai_compat")
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def simplify_schema(schema):
    """Make one of our schemas safe for providers with a stricter or narrower JSON-Schema dialect.

    Our own schemas say a field may be `null` (`"type": ["string","null"]`, an enum containing None). Several
    providers reject unions and null enum members. Every such field is optional anyway, so dropping `null` from
    the advertised schema loses nothing; our registry still validates against the ORIGINAL schema, which keeps
    accepting a stray null.
    """
    if isinstance(schema, list):
        return [simplify_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, list):
            non_null = [t for t in value if t != "null"]
            out[key] = non_null[0] if len(non_null) == 1 else non_null
        elif key == "enum" and isinstance(value, list):
            out[key] = [v for v in value if v is not None]
        else:
            out[key] = simplify_schema(value)
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": simplify_schema(t["input_schema"])}} for t in tools]


def strip_reasoning(text: str) -> str:
    """Some models put their reasoning in the reply inside <think> tags. Drop it; if the reply was cut off in
    the middle of thinking, there is no answer, so return nothing."""
    text = _THINK.sub("", text)
    if re.search(r"<think>", text, re.I):
        return ""
    return text.strip()


def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):          # some servers return [{"type": "text", "text": "..."}]
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


class OpenAICompatProvider:
    """`auth` is "bearer" (Authorization: Bearer <key>), "header:<Name>" (the key in a custom header, as
    Sarvam's `api-subscription-key` does), or "none" (a local server)."""

    def __init__(self, name: str, base_url: str, model: str, api_key: str | None = None, auth: str = "bearer",
                 retries: int = 2, timeout: float = 30.0, transport: httpx.BaseTransport | None = None) -> None:
        self.name = name
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.auth, self.retries = model, api_key or "", auth, retries
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def _headers(self) -> dict:
        if self.auth == "none":
            return {}
        if not self.api_key:
            raise LlmUnavailableError(f"{self.name}: no API key is set.")
        if self.auth == "bearer":
            return {"Authorization": f"Bearer {self.api_key}"}
        if self.auth.startswith("header:"):
            return {self.auth.split(":", 1)[1]: self.api_key}
        raise LlmUnavailableError(f"{self.name}: unknown auth style {self.auth!r}")

    def _post(self, body: dict) -> dict:
        headers = self._headers()
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.post(self.url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                last = exc
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        raise LlmUnavailableError(f"{self.name}: non-JSON response: {resp.text[:200]!r}")
                if resp.status_code in (401, 403):
                    raise LlmUnavailableError(f"{self.name} rejected the API key (HTTP {resp.status_code}).")
                if resp.status_code in (400, 404, 422):
                    raise LlmUnavailableError(f"{self.name} rejected the request (HTTP {resp.status_code}): "
                                              f"{resp.text[:300]}")
                last = LlmUnavailableError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}")
            if attempt < self.retries:
                time.sleep(0.5 * 2 ** attempt)
        raise LlmUnavailableError(f"{self.name} failed after {self.retries + 1} attempts: {last}")

    @staticmethod
    def _message(data: dict) -> dict:
        try:
            return data["choices"][0]["message"] or {}
        except (KeyError, IndexError, TypeError):
            raise LlmUnavailableError(f"unexpected response shape: {str(data)[:200]}")

    def choose_actions(self, system: str, tools: list[dict], user: str, max_tokens: int = 1024) -> list[ToolCall]:
        data = self._post({"model": self.model, "max_tokens": max_tokens, "tools": to_openai_tools(tools),
                           "tool_choice": "auto",
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        calls: list[ToolCall] = []
        for tc in self._message(data).get("tool_calls") or []:
            fn = (tc or {}).get("function") or {}
            name, raw = fn.get("name"), fn.get("arguments")
            if not name:
                continue
            if raw in (None, ""):
                args = {}                                    # a tool with no arguments
            elif isinstance(raw, dict):
                args = raw                                   # some servers decode it for us
            else:
                try:
                    args = json.loads(raw)
                except (ValueError, TypeError):
                    log.warning("%s sent malformed arguments for %s: %r", self.name, name, str(raw)[:120])
                    continue                                 # an unusable call is no call
            calls.append(ToolCall(name, args))
        log.info("%s model=%s calls=%s", self.name, self.model, [c.name for c in calls])
        return calls

    def complete(self, system: str, user: str, max_tokens: int = 512) -> str:
        data = self._post({"model": self.model, "max_tokens": max_tokens,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        return strip_reasoning(_text_of(self._message(data).get("content")))
