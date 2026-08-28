"""Tool registry, dispatch, and JSON encoding of the MCP surface."""

import json
import math

import pytest

from src import server
from src.mcp_compat import SDK_GENERATION


def schema_of(tool):
    return getattr(tool, "input_schema", None) or tool.inputSchema


TOOL_NAMES = {t.name for t in server.TOOLS}


def test_every_tool_is_routable():
    """A tool advertised but not handled would fall through to 'unknown_tool'."""
    import inspect

    source = inspect.getsource(server.call_tool)
    for name in TOOL_NAMES:
        assert f'"{name}"' in source, f"{name} is advertised but never dispatched"


def test_tool_names_are_unique():
    assert len([t.name for t in server.TOOLS]) == len(TOOL_NAMES)


def test_schemas_are_well_formed():
    for tool in server.TOOLS:
        schema = schema_of(tool)
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties", {}), dict)
        assert tool.description and len(tool.description) > 20
        for req in schema.get("required", []):
            assert req in schema["properties"], f"{tool.name}: {req} required but not declared"


def test_new_scanner_tools_expose_their_tunables():
    """These three shipped with empty schemas, so their filters were unreachable."""
    expected = {
        "scan_mean_reversion": {"rsi_threshold", "min_volume", "min_below_sma20_pct"},
        "scan_volatility_squeeze": {"min_volume", "squeeze_tolerance"},
        "scan_volume_accumulation": {"min_volume", "vol_multiple", "max_spread_pct"},
    }
    by_name = {t.name: t for t in server.TOOLS}
    for name, params in expected.items():
        assert set(schema_of(by_name[name])["properties"]) == params


def test_thesis_tool_requires_position_value():
    tool = next(t for t in server.TOOLS if t.name == "evaluate_and_log_thesis")
    schema = schema_of(tool)
    assert "position_value_idr" in schema["properties"]
    assert "position_value_idr" in schema["required"]


async def decode(name, args):
    out = await server.call_tool(name, args)
    assert len(out) == 1 and out[0].type == "text"
    return json.loads(out[0].text)


async def test_unknown_tool_returns_structured_error():
    r = await decode("no_such_tool", {})
    assert r["error"] is True
    assert r["error_type"] == "unknown_tool"


async def test_missing_required_argument_is_reported_as_invalid_arguments():
    r = await decode("get_stock_price", {})
    assert r["error"] is True
    assert r["error_type"] == "invalid_arguments"
    assert "ticker" in r["message"]


async def test_omitted_arguments_object_does_not_crash():
    """Clients may send no `arguments` key at all for a no-arg tool."""
    out = await server.call_tool("no_such_tool", None)
    assert json.loads(out[0].text)["error_type"] == "unknown_tool"


async def test_handler_exception_becomes_an_error_payload(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(server, "get_stock_price", boom)
    r = await decode("get_stock_price", {"ticker": "BBCA"})
    assert r["error"] is True
    assert r["error_type"] == "internal_error"
    assert "upstream exploded" in r["message"]


async def test_tool_results_are_json_encodable_with_nan(monkeypatch):
    """NaN is not valid JSON; it must be sanitised to null, not emitted raw."""
    async def nan_result(*a, **k):
        return {"price": float("nan"), "nested": {"rsi": float("nan")},
                "items": [1.0, float("nan")], "ok": 5.0}

    monkeypatch.setattr(server, "get_stock_price", nan_result)
    out = await server.call_tool("get_stock_price", {"ticker": "BBCA"})
    text = out[0].text
    assert "NaN" not in text

    r = json.loads(text)
    assert r["price"] is None
    assert r["nested"]["rsi"] is None
    assert r["items"] == [1.0, None]
    assert r["ok"] == 5.0


def test_sanitize_nans_leaves_finite_values_untouched():
    payload = {"a": 1.5, "b": [2, "x", None], "c": {"d": 0.0}}
    assert server._sanitize_nans(payload) == payload
    assert server._sanitize_nans(float("nan")) is None
    assert math.isinf(server._sanitize_nans(float("inf")))


async def test_list_tools_returns_the_registry():
    assert await server.list_tools() is server.TOOLS


def test_compat_layer_detected_an_sdk_generation():
    assert SDK_GENERATION in {"1.x", "2.x"}


def test_server_builds_against_the_installed_sdk():
    from src.mcp_compat import build_server

    built = build_server("idx-mcp-test", "9.9.9", server.list_tools, server.call_tool)
    assert built is not None
