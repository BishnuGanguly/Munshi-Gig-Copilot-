"""Action registry: every capability the assistant has is declared once here.

An action has a name, a description, a JSON input schema, and handlers. The same declaration serves
  - the model-driven router (brain.py calls `execute` with the arguments the model chose),
  - the model's tool list (`tool_definitions()`; the model only picks an action and fills its arguments,
    and our code validates and runs it),
  - an MCP server later (a thin adapter over the same definitions).

Writes never apply immediately: a write action's `run` PROPOSES (stores a pending action and returns a
read-back); `commit` runs only after the user confirms. `user_id` comes from the Context, never from arguments,
so a model or client cannot act as another user.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from . import tools


class ActionError(ValueError):
    """Unknown action or arguments that do not match the action's schema."""


@dataclass
class Context:
    conn: sqlite3.Connection
    user_id: str
    now: datetime

    @property
    def today(self) -> date:
        return self.now.date()


@dataclass
class Outcome:
    reply: str
    entries: list = field(default_factory=list)
    intent: str = "unknown"
    warnings: list = field(default_factory=list)
    actions: list = field(default_factory=list)   # audit trail: [{"name": ..., "args": ...}]
    facts: dict | None = None    # after a save: computed facts the model may choose an insight from


@dataclass
class ActionSpec:
    name: str
    description: str
    input_schema: dict
    run: Callable[[Context, dict], Outcome]                   # read-only: executes; write: proposes
    read_only: bool = True
    commit: Callable[[Context, dict], Outcome] | None = None  # write actions: executes a confirmed proposal


# ------------------------------------------------------------------ argument validation (small JSON-schema subset)
_PY = {"string": str, "boolean": bool, "array": list, "object": dict, "null": type(None)}


def _is_type(name: str, val) -> bool:
    if name == "number":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if name == "integer":
        return isinstance(val, int) and not isinstance(val, bool)
    return isinstance(val, _PY[name])


def _check(spec: dict, val, path: str) -> list[str]:
    errors: list[str] = []
    types = spec.get("type")
    if types:
        names = types if isinstance(types, list) else [types]
        if not any(_is_type(n, val) for n in names):
            return [f"{path}: expected {'/'.join(names)}"]
    if "enum" in spec and val not in spec["enum"]:
        errors.append(f"{path}: must be one of {spec['enum']}")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if "minimum" in spec and val < spec["minimum"]:
            errors.append(f"{path}: must be at least {spec['minimum']}")
        if "maximum" in spec and val > spec["maximum"]:
            errors.append(f"{path}: must be at most {spec['maximum']}")
    if isinstance(val, list):
        if "minItems" in spec and len(val) < spec["minItems"]:
            errors.append(f"{path}: needs at least {spec['minItems']} item(s)")
        if "maxItems" in spec and len(val) > spec["maxItems"]:
            errors.append(f"{path}: at most {spec['maxItems']} items")
        for i, item in enumerate(val):
            errors += _check(spec.get("items", {}), item, f"{path}[{i}]")
    if isinstance(val, dict) and "properties" in spec:
        errors += validate_args(spec, val, path)
    return errors


def validate_args(schema: dict, args, path: str = "arguments") -> list[str]:
    """Return a list of problems (empty when valid)."""
    if not isinstance(args, dict):
        return [f"{path}: expected an object"]
    errors = [f"{path}: missing '{k}'" for k in schema.get("required", []) if k not in args]
    props = schema.get("properties", {})
    for key, val in args.items():
        if key in props:
            errors += _check(props[key], val, f"{path}.{key}")
        elif schema.get("additionalProperties", True) is False:
            errors.append(f"{path}: unknown field '{key}'")
    return errors


# ------------------------------------------------------------------ registry
class Registry:
    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        if spec.name in self._actions:
            raise ValueError(f"action {spec.name!r} already registered")
        if not spec.read_only and spec.commit is None:
            raise ValueError(f"write action {spec.name!r} needs a commit handler")
        self._actions[spec.name] = spec

    def get(self, name: str) -> ActionSpec:
        try:
            return self._actions[name]
        except KeyError:
            raise ActionError(f"unknown action {name!r}") from None

    def names(self) -> list[str]:
        return list(self._actions)

    def tool_definitions(self) -> list[dict]:
        """The action list in LLM tool-calling format."""
        return [{"name": a.name, "description": a.description, "input_schema": a.input_schema}
                for a in self._actions.values()]

    def execute(self, name: str, args: dict, ctx: Context) -> Outcome:
        """Validate arguments, then run (read-only) or propose (write). Raises ActionError when invalid."""
        spec = self.get(name)
        problems = validate_args(spec.input_schema, args)
        if problems:
            raise ActionError(f"{name}: " + "; ".join(problems))
        outcome = spec.run(ctx, args)
        outcome.actions.insert(0, {"name": name, "args": args, "stage": "run" if spec.read_only else "proposed"})
        return outcome

    def commit(self, ctx: Context, pending: dict) -> Outcome:
        """Execute a confirmed pending action (its payload carries the action name)."""
        spec = self.get(pending["action"])
        tools.clear_pending(ctx.conn, ctx.user_id)
        outcome = spec.commit(ctx, pending)
        outcome.actions.insert(0, {"name": spec.name, "stage": "committed"})
        return outcome
