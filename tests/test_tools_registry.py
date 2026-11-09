from datetime import date
from decimal import Decimal
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from tests.test_tools_sql import snapshot
from tools import ALL_TOOLS, default_registry
from tools.common import engine
from tools.registry import Tool, ToolError, ToolRegistry, ToolResult, jsonable

EXAMPLE_ARGS: dict[str, dict[str, Any]] = {
    "list_tickers": {},
    "search_news": {"ticker": "NVDA", "days": 7, "keyword": "chip", "limit": 5},
    "sentiment_trend": {"ticker": "NVDA", "days": 7},
    "price_history": {"ticker": "NVDA", "days": 5},
    "compare_tickers": {"tickers": ["NVDA", "AMD"], "days": 7},
    "run_sql": {"query": "SELECT count(*) FROM articles"},
    "web_search": {"query": "nvidia export rules"},
}


def test_every_tool_is_registered_once() -> None:
    names = default_registry().names()
    assert names == list(EXAMPLE_ARGS)
    assert len(set(names)) == len(ALL_TOOLS)


@pytest.mark.parametrize("schema", default_registry().schemas(), ids=lambda s: s["name"])
def test_schemas_are_valid_flat_json_schema(schema: dict[str, Any]) -> None:
    parameters = schema["parameters"]
    jsonschema.Draft202012Validator.check_schema(parameters)
    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    assert "$ref" not in str(parameters) and "anyOf" not in str(parameters)
    assert "title" not in parameters
    for name, prop in parameters["properties"].items():
        assert prop.get("description"), f"{schema['name']}.{name} has no description"
        assert "type" in prop
    jsonschema.validate(EXAMPLE_ARGS[schema["name"]], parameters)


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_descriptions_are_written_for_a_model(tool: Tool) -> None:
    assert len(tool.description) >= 100
    assert tool.description.strip() == tool.description


def test_optional_fields_are_not_required() -> None:
    schema = next(s for s in default_registry().schemas() if s["name"] == "search_news")
    params = schema["parameters"]
    assert params["required"] == ["ticker"]
    assert params["properties"]["start_date"] == {
        "type": "string",
        "format": "date",
        "description": "First day to include, YYYY-MM-DD (US Eastern date).",
    }
    assert params["properties"]["limit"]["maximum"] == 25


class _Args(BaseModel):
    x: int = 0


def _registry_with(fn: Any) -> ToolRegistry:
    return ToolRegistry([Tool(name="t", description="test tool", input_model=_Args, fn=fn)])


def test_unknown_tool() -> None:
    result = default_registry().call("delete_everything", {})
    assert not result["ok"]
    assert "Available tools: list_tickers" in result["error"]


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (ToolError("Ticker not found"), "Ticker not found"),
        (RuntimeError("boom"), "Tool t failed with an internal error"),
        (OperationalError("SELECT", {}, Exception("connection refused")), "connection refused"),
    ],
)
def test_call_never_raises(exc: Exception, message: str) -> None:
    def fail(_: _Args) -> ToolResult:
        raise exc

    result = _registry_with(fail).call("t", {})
    assert result["ok"] is False
    assert message in result["error"]


def test_result_envelope_is_json_ready() -> None:
    def ok(_: _Args) -> ToolResult:
        return ToolResult(
            data={"n": Decimal("1.23456"), "d": date(2026, 6, 30)}, as_of=date.today()
        )

    result = _registry_with(ok).call("t", {"x": 1})
    assert result == {
        "ok": True,
        "data": {"n": 1.2346, "d": "2026-06-30"},
        "as_of": date.today().isoformat(),
        "sources": [],
    }


def test_jsonable_handles_nested_values() -> None:
    assert jsonable({"a": [(1, 2.123456)], 3: None}) == {"a": [[1, 2.1235]], "3": None}


def test_duplicate_tool_names_are_refused() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        ToolRegistry([ALL_TOOLS[0], ALL_TOOLS[0]])


def test_nested_input_models_are_refused() -> None:
    class Inner(BaseModel):
        y: int

    class Outer(BaseModel):
        inner: Inner

    with pytest.raises(ValueError, match="flat"):
        Tool(name="n", description="d", input_model=Outer, fn=lambda _: None).json_schema()


def test_tools_connect_as_the_read_only_role(tools_db: Engine) -> None:
    with engine().connect() as conn:
        user, read_only = conn.execute(
            text("SELECT current_user, current_setting('transaction_read_only')")
        ).one()
    assert (user, read_only) == ("agent_readonly", "on")


def test_no_tool_changes_the_database(tools_db: Engine) -> None:
    registry = default_registry()
    before = snapshot(tools_db)

    results = {name: registry.call(name, args) for name, args in EXAMPLE_ARGS.items()}

    assert snapshot(tools_db) == before
    assert all(r["ok"] for name, r in results.items() if name != "web_search"), results
