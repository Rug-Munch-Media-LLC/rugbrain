"""MCP surface for the fleet retrieval layers: registration + envelope contract."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastmcp", reason="install the [mcp] extra")

import rugbrain.rugbrain_mcp.tools_layers as tl


def _zoekt(ok: bool, data: Any = None, code: str | None = None):
    def _fake(query: str, files_only: bool = False):
        return {
            "ok": ok,
            "data": data,
            "source": "zoekt",
            "error": None if code is None else {"code": code, "detail": "socket.timeout"},
            "fallback": None if code is None else "rg-search codebase_search_text",
        }

    return _fake


@pytest.mark.anyio
async def test_three_layer_tools_registered() -> None:
    """The three layer tools are published by the FastMCP server."""
    names = {t.name for t in await tl.mcp.list_tools()}
    assert {"exact_search", "symbol_info", "compress_text"} <= names


def test_registration_reaches_server_module() -> None:
    import rugbrain.rugbrain_mcp.server as srv

    assert srv.exact_search is tl.exact_search
    assert srv.symbol_info is tl.symbol_info
    assert srv.compress_text is tl.compress_text


def test_exact_search_success_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tl, "zoekt_search", _zoekt(True, {"query": "q", "count": 1, "empty": False, "files": [{"file": "a.py"}]}))
    out = tl.exact_search("q")
    assert out["status"] == "ok" and out["tool"] == "exact_search" and out["source"] == "zoekt"
    assert out["error"] is None and out["fallback"] is None
    assert out["data"]["count"] == 1


def test_exact_search_timeout_is_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tl, "zoekt_search", _zoekt(False, None, "timeout"))
    out = tl.exact_search("q")
    assert out["status"] == "refused" and out["data"] is None
    assert out["error"]["code"] == "timeout" and out["fallback"]


def test_empty_result_is_explicit_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tl, "zoekt_search", _zoekt(True, {"query": "nope", "count": 0, "empty": True, "files": []}))
    out = tl.exact_search("nope")
    assert out["status"] == "ok"
    assert out["data"]["empty"] is True and out["data"]["count"] == 0


def test_ok_without_data_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tl, "zoekt_search", _zoekt(True, None))
    out = tl.exact_search("q")
    assert out["status"] == "refused" and out["error"]["code"] == "bad_response"


def test_symbol_info_requires_project() -> None:
    out = tl.symbol_info("zoekt_search")
    assert out["status"] == "refused" and out["data"] is None
    assert out["error"]["code"] == "project_not_activated"


def test_symbol_info_success_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tl,
        "serena_symbols",
        lambda project, pattern: {
            "ok": True,
            "data": {"project": project, "name_path_pattern": pattern, "symbols": "a.py:1 def x", "empty": False},
            "source": "serena",
            "error": None,
            "fallback": None,
        },
    )
    out = tl.symbol_info("zoekt_search", project="/repo")
    assert out["status"] == "ok" and out["source"] == "serena"
    assert out["data"]["name_path_pattern"] == "zoekt_search"
    assert out["data"]["empty"] is False


def test_symbol_info_unavailable_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tl,
        "serena_symbols",
        lambda project, pattern: {"ok": False, "data": None, "source": "serena", "error": {"code": "unavailable", "detail": "down"}, "fallback": "zoekt"},
    )
    out = tl.symbol_info("X", project="/repo")
    assert out["status"] == "refused" and out["error"]["code"] == "unavailable"


def test_compress_text_success_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tl,
        "compress",
        lambda text, ratio: {
            "ok": True,
            "data": {"compressed": "short", "ratio_requested": ratio, "ratio_actual": 0.33, "endpoint": "/compress"},
            "source": "cc-compress",
            "error": None,
            "fallback": None,
        },
    )
    out = tl.compress_text("a long original sentence", 0.4)
    assert out["status"] == "ok" and out["source"] == "cc-compress"
    assert out["data"]["ratio_actual"] == 0.33 and out["data"]["ratio_requested"] == 0.4


def test_compress_text_unavailable_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tl,
        "compress",
        lambda text, ratio: {"ok": False, "data": None, "source": "cc-compress", "error": {"code": "unavailable", "detail": "probed"}, "fallback": "keep RAW"},
    )
    out = tl.compress_text("text")
    assert out["status"] == "refused" and out["data"] is None
    assert out["error"]["code"] == "unavailable"
