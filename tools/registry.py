"""Tool registry: names, model-facing descriptions, input schemas and one result format.

Every call returns either
    {"ok": true, "data": ..., "as_of": "YYYY-MM-DD", "sources": [...]}
or
    {"ok": false, "error": "<message the model can act on>"}
and never raises.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """An expected failure; the message is shown to the model as-is."""


@dataclass
class ToolResult:
    data: Any
    as_of: date
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any], ToolResult]

    def json_schema(self) -> dict[str, Any]:
        return clean_schema(self.input_model.model_json_schema())


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """Provider-neutral declarations: name, description and JSON Schema parameters."""
        return [
            {"name": t.name, "description": t.description, "parameters": t.json_schema()}
            for t in self._tools.values()
        ]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return error(f"Unknown tool '{name}'. Available tools: {', '.join(self._tools)}.")
        try:
            params = tool.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            return error(f"Invalid arguments for {name}: {describe_validation_error(exc)}")
        try:
            result = tool.fn(params)
        except ToolError as exc:
            return error(str(exc))
        except DBAPIError as exc:
            logger.warning("Database error in tool %s: %s", name, exc)
            return error(f"The database query failed: {first_line(exc.orig)}")
        except Exception:
            logger.exception("Unexpected error in tool %s", name)
            return error(f"Tool {name} failed with an internal error. Try different arguments.")
        return {
            "ok": True,
            "data": jsonable(result.data),
            "as_of": result.as_of.isoformat(),
            "sources": jsonable(result.sources),
        }


def error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def first_line(exc: BaseException | None) -> str:
    return str(exc).strip().splitlines()[0] if exc and str(exc).strip() else "unknown error"


def describe_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field_path = ".".join(str(p) for p in err["loc"]) or "arguments"
        parts.append(f"{field_path}: {err['msg']}")
    return "; ".join(parts)


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return round(float(value), 4)
    if isinstance(value, float):
        return round(value, 4)
    if value is None or isinstance(value, str | int | bool):
        return value
    return str(value)


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce a Pydantic schema to a flat subset every function-calling provider accepts.

    Drops titles, turns `X | None` into plain `X` (optional fields are simply not
    required) and refuses `$ref`, so input models must stay flat.
    """
    if "$defs" in schema or "$ref" in str(schema):
        raise ValueError("Tool input models must be flat (no nested models or $ref).")
    return _clean_node(schema)


def _clean_node(node: dict[str, Any]) -> dict[str, Any]:
    node = {k: v for k, v in node.items() if k != "title"}
    if "anyOf" in node:
        options = [o for o in node.pop("anyOf") if o.get("type") != "null"]
        if len(options) != 1:
            raise ValueError(f"Unsupported union in tool schema: {options}")
        node = {**_clean_node(options[0]), **node}
        if "default" in node and node["default"] is None:
            del node["default"]
    if "properties" in node:
        node["properties"] = {k: _clean_node(v) for k, v in node["properties"].items()}
    if isinstance(node.get("items"), dict):
        node["items"] = _clean_node(node["items"])
    return node
