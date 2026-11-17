"""Tool registry with JSON-Schema-style input validation.

Validation is deliberately strict and small: required fields, primitive types,
enums, numeric bounds and string length. The model supplies these arguments, so
they are untrusted input.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], str]
    read_only: bool = True


class Registry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, schema: dict, read_only: bool = True):
        def deco(fn):
            self._tools[name] = Tool(name, description, schema, fn, read_only)
            return fn
        return deco

    def has(self, name: str) -> bool:
        return name in self._tools

    def describe(self) -> list[dict]:
        return [{"name": t.name, "description": t.description, "inputSchema": t.schema,
                 "annotations": {"readOnlyHint": t.read_only, "destructiveHint": False,
                                 "idempotentHint": t.read_only, "openWorldHint": False}}
                for t in self._tools.values()]

    def call(self, name: str, args: dict) -> dict:
        tool = self._tools[name]
        problems = validate(args, tool.schema)
        if problems:
            return _content("invalid arguments: " + "; ".join(problems), is_error=True)
        try:
            return _content(tool.fn(args))
        except ToolError as exc:
            return _content(str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001 - do not leak internals to the model
            return _content(f"tool failed: {type(exc).__name__}", is_error=True)


class ToolError(Exception):
    """A failure the model should see and can act on."""


def _content(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def validate(args: dict, schema: dict) -> list[str]:
    problems = []
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in args:
            problems.append(f"missing required field '{req}'")
    for key, val in args.items():
        spec = props.get(key)
        if spec is None:
            problems.append(f"unexpected field '{key}'")
            continue
        problems += _check(key, val, spec)
    return problems


def _check(key: str, val: Any, spec: dict) -> list[str]:
    t = spec.get("type")
    py = _TYPES.get(t)
    if py is not None:
        if isinstance(val, bool) and t in ("integer", "number"):
            return [f"'{key}' must be {t}"]
        if not isinstance(val, py):
            return [f"'{key}' must be {t}"]
    out = []
    if "enum" in spec and val not in spec["enum"]:
        out.append(f"'{key}' must be one of {spec['enum']}")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if "minimum" in spec and val < spec["minimum"]:
            out.append(f"'{key}' must be >= {spec['minimum']}")
        if "maximum" in spec and val > spec["maximum"]:
            out.append(f"'{key}' must be <= {spec['maximum']}")
    if isinstance(val, str):
        if "maxLength" in spec and len(val) > spec["maxLength"]:
            out.append(f"'{key}' longer than {spec['maxLength']} characters")
        if "minLength" in spec and len(val) < spec["minLength"]:
            out.append(f"'{key}' shorter than {spec['minLength']} characters")
    return out
