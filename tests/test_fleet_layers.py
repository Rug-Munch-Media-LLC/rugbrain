"""Fleet layer adapters: success fixtures, typed refusals, no silent empties."""

from __future__ import annotations

import json
import socket
from typing import Any, ClassVar

import pytest

import importlib

cmod = importlib.import_module("rugbrain.fleet_layers.compress")
smod = importlib.import_module("rugbrain.fleet_layers.serena")
zmod = importlib.import_module("rugbrain.fleet_layers.zoekt")

ZOEKT_FIXTURE: dict[str, Any] = {
    "result": {
        "FileMatches": [
            {"FileName": "src/rugbrain/fleet_layers/zoekt.py", "Repository": "rugbrain", "ChunkMatches": [{"Content": "def zoekt_search"}]}
        ]
    }
}


def _zoekt_stub(body: dict[str, Any], status: int = 200):
    def _fake(url: str, **_: Any):
        return status, {}, json.dumps(body).encode()

    return _fake


def test_zoekt_success_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _zoekt_stub(ZOEKT_FIXTURE))
    out = zmod.zoekt_search("zoekt_search")
    assert out["ok"] is True and out["source"] == "zoekt" and out["error"] is None
    assert out["data"]["count"] == 1 and out["data"]["empty"] is False
    assert out["data"]["files"][0]["file"].endswith("zoekt.py")


def test_zoekt_timeout_is_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(url: str, **_: Any):
        raise socket.timeout("timed out")

    monkeypatch.setattr(zmod, "_http_json", _boom)
    out = zmod.zoekt_search("x")
    assert out["ok"] is False and out["data"] is None
    assert out["error"]["code"] == "timeout" and out["fallback"]


def test_zoekt_unauthorized_is_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _zoekt_stub({}, status=401))
    out = zmod.zoekt_search("x")
    assert out["ok"] is False and out["error"]["code"] == "unauthorized"


def test_zoekt_empty_result_is_explicit_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _zoekt_stub({"result": {"FileMatches": []}}))
    out = zmod.zoekt_search("nothing-matches-this")
    assert out["ok"] is True
    assert out["data"]["files"] == [] and out["data"]["empty"] is True and out["data"]["count"] == 0


def _serena_stub(calls: ClassVar[list[Any]]):
    def _fake(session: str | None, payload: dict[str, Any], timeout: float = 8.0):
        if payload.get("method") == "tools/call":
            calls.append((payload.get("params") or {}).get("name"))
        else:
            calls.append(payload.get("method"))
        if payload.get("method") == "initialize":
            return 200, {"mcp-session-id": "sid-1"}, {"result": {"serverInfo": {"name": "Serena"}}}
        if (payload.get("params") or {}).get("name") == "find_symbol":
            return 200, {}, {"result": {"content": [{"type": "text", "text": "zoekt.py:9 def zoekt_search"}]}}
        return 200, {}, {"result": {}}

    return _fake


def test_serena_success_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(smod, "_rpc", _serena_stub(calls))
    out = smod.serena_symbols("/srv/work/wt/rb-layers24", "zoekt_search")
    assert out["ok"] is True and out["data"]["empty"] is False
    assert "zoekt_search" in out["data"]["symbols"]
    assert calls[:4] == ["initialize", "notifications/initialized", "initial_instructions", "activate_project"]
    assert calls[4] == "find_symbol"


def test_serena_timeout_is_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(session: str | None, payload: dict[str, Any], timeout: float = 8.0):
        raise socket.timeout("timed out")

    monkeypatch.setattr(smod, "_rpc", _boom)
    out = smod.serena_symbols("/repo", "Symbol")
    assert out["ok"] is False and out["error"]["code"] == "timeout"


def test_serena_missing_session_is_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smod, "_rpc", lambda s, p, timeout=8.0: (200, {}, {"result": {"serverInfo": {}}}))
    out = smod.serena_symbols("/repo", "Symbol")
    assert out["ok"] is False and out["error"]["code"] == "unauthorized"


def _compress_stub(status: int, payload: dict[str, Any]):
    def _fake(url: str, **_: Any):
        return status, {}, json.dumps(payload).encode()

    return _fake


def test_compress_success_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmod, "_http_json", _compress_stub(200, {"compressed_prompt": "short", "origin_tokens": 100, "compressed_tokens": 40}))
    out = cmod.compress("a much longer original sentence", 0.4)
    assert out["ok"] is True and out["data"]["compressed"] == "short"
    assert out["data"]["ratio_actual"] < 1.0 and out["data"]["endpoint"]


def test_compress_no_route_is_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmod, "_http_json", _compress_stub(404, {"error": "not found"}))
    out = cmod.compress("text")
    assert out["ok"] is False and out["error"]["code"] == "unavailable"
    assert "probed" in out["error"]["detail"] and "RAW" in out["fallback"]


def test_compress_echo_is_refused_not_faked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmod, "_http_json", _compress_stub(200, {"compressed_prompt": "same text"}))
    out = cmod.compress("same text")
    assert out["ok"] is False and out["error"]["code"] == "verify_or_return"
    assert out["data"] is None


def test_compress_unauthorized_is_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmod, "_http_json", _compress_stub(401, {"error": "unauthorized"}))
    out = cmod.compress("text")
    assert out["ok"] is False and out["error"]["code"] == "unauthorized"
