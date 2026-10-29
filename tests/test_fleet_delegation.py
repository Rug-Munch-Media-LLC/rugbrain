"""Delegation: owner first, typed refusal, fallback, and the estate surface."""

from __future__ import annotations

import json

import pytest

from rugbrain.fleet_layers import delegate as dmod
from rugbrain.fleet_layers import native as nmod
from rugbrain.fleet_layers import zoekt as zmod

ZOEKT_OK = {"result": {"FileMatches": [
    {"FileName": "src/rugbrain/fleet_layers/zoekt.py", "Repository": "rugbrain",
     "ChunkMatches": [{"Content": "def zoekt_search"}]}]}}
NATIVE_OK = {"results": [{"id": "chunk-1", "score": 0.91}]}


def _stub(body: dict, status: int = 200):
    def _fake(url: str, **_: object):
        return status, {}, json.dumps(body).encode()

    return _fake


def _raise(exc: BaseException):
    def _fake(url: str, **_: object):
        raise exc

    return _fake


def test_exact_delegates_to_zoekt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _stub(ZOEKT_OK))
    out = dmod.delegate("exact", query="zoekt_search")
    assert out["ok"] is True and out["source"] == "zoekt"
    assert out["delegated_to"] == "zoekt" and out["fell_back_from"] is None
    assert out["data"]["files"][0]["file"].endswith("zoekt.py")


def test_owner_refusal_falls_back_to_own_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _stub({}, status=401))
    monkeypatch.setattr(nmod, "_http_json", _stub(NATIVE_OK))
    out = dmod.delegate("exact", query="RMI_RAG_API_KEY")
    assert out["ok"] is True and out["source"] == "rugbrain"
    assert out["fell_back_from"] == "zoekt" and out["delegated_to"] == "rugbrain"
    assert out["owner_error"]["code"] == "unauthorized"
    assert out["data"]["results"][0]["id"] == "chunk-1"


def test_all_layers_down_is_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _raise(TimeoutError("owner down")))
    monkeypatch.setattr(nmod, "_http_json", _raise(OSError("native down")))
    out = dmod.delegate("exact", query="x")
    assert out["ok"] is False and out["data"] is None
    assert out["error"]["code"] == "all_layers_unavailable"
    assert out["owner_error"]["code"] == "timeout" and out["fallback_error"]["code"] == "unavailable"


def test_unsupported_kind_refuses_without_calling_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _raise(AssertionError("must not be called")))
    out = dmod.delegate("graph", query="x")
    assert out["ok"] is False and out["error"]["code"] == "unsupported_kind"


def test_delegate_lookup_reports_answering_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zmod, "_http_json", _stub(ZOEKT_OK))
    from rugbrain.rugbrain_mcp.tools_delegate import delegate_lookup
    out = delegate_lookup("exact", "zoekt_search")
    assert out["status"] == "ok" and out["source"] == "zoekt" and out["delegated_to"] == "zoekt"


def test_delegate_lookup_symbol_needs_project() -> None:
    from rugbrain.rugbrain_mcp.tools_delegate import delegate_lookup
    out = delegate_lookup("symbol", "RugBrainClient.search")
    assert out["status"] == "refused" and out["error"]["code"] == "project_not_activated"
    assert out["data"] is None


def test_route_question_names_six_external_apps() -> None:
    from rugbrain.rugbrain_mcp.tools_route import route_question
    out = route_question("where is the exact string RMI_RAG_API_KEY", probe=False)
    apps = set(out["estate_apps"]) - {"rugbrain"}
    assert len(apps) >= 6, apps
    for required in ("zoekt", "serena", "context7", "deepwiki", "code-intel", "markitdown", "repomix"):
        assert required in apps, required
    assert out["layer"] == "zoekt_exact"
