"""Tests for the data-driven routing layer (rugbrain route_question)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain import layer_probe
from rugbrain.routing_classify import classify
from rugbrain.routing_table import TABLE_PATH, load_table

FIXTURE = Path(__file__).parent / "fixtures" / "layer_inventory.json"


def _inventory() -> dict:
    return json.loads(FIXTURE.read_text())


def test_table_loads() -> None:
    table = load_table()
    assert table["layers"] and table["shapes"]
    assert TABLE_PATH.exists()
    for layer in table["layers"].values():
        assert layer["call"]["tool"]


def test_table_is_editable_data() -> None:
    table = load_table()
    for shape in table["shapes"]:
        assert shape["keywords"], shape["id"]
        assert shape["layer"] in table["layers"]


def test_unknown_question_returns_safe_default() -> None:
    table = load_table()
    picked = classify("qqq zzz unrelated gibberish", table)
    assert picked["layer"] == table["default_layer"]
    assert picked["confidence"] == "default"


def test_five_canonical_shapes_route() -> None:
    table = load_table()
    cases = {
        "where is the exact string RMI_RAG_API_KEY": "zoekt_exact",
        "who calls the classify function in routing": "serena_symbols",
        "how do we build a retrieval provider": "context_crypto_curated",
        "what is the current FastAPI library version api": "context7",
        "what is the architecture of this repo, show the call graph": "metamcp_code",
    }
    for question, layer in cases.items():
        assert classify(question, table)["layer"] == layer, question


def test_unreachable_layer_degrades_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layer_probe.socket, "create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    layer_probe.clear_cache()
    result = layer_probe.probe_layer("http://127.0.0.1:1")
    assert result["reachable"] is False
    from rugbrain.rugbrain_mcp.tools_route import route_question
    out = route_question("where is the exact string FOO", probe=True)
    assert out["degraded"] is True
    assert out["use_instead"]["layer"]


def test_no_invented_tool_names() -> None:
    inv = _inventory()
    servers, schemas = inv["servers"], inv["schemas"]
    table = load_table()
    checked = 0
    for name, layer in table["layers"].items():
        server = layer["server"]
        if server not in servers:
            pytest.fail(f"layer {name} targets server {server} absent from the captured inventory")
        assert layer["call"]["tool"] in servers[server], f"{name}: {layer['call']['tool']} not served by {server}"
        declared = set(schemas.get(server, {}).get(layer["call"]["tool"], []))
        if declared:
            unknown = set(layer["call"].get("args", {})) - declared
            assert not unknown, f"{name}: invented args {unknown}"
            checked += 1
    assert checked >= 5


def test_route_question_returns_call_shape() -> None:
    from rugbrain.rugbrain_mcp.tools_route import route_question
    out = route_question("where is the exact string RMI_RAG_API_KEY", probe=False)
    assert out["layer"] == "zoekt_exact"
    assert out["call"]["tool"] == "search"
    assert out["degraded"] is False
