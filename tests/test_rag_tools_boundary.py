"""Public output boundary for RAG tool routes."""

from __future__ import annotations

from typing import Any

import pytest

from rugbrain.routes import rag_tools


@pytest.mark.asyncio
async def test_snapshot_query_response_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    import rugbrain.parquet_analytics as analytics

    def fake_query(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "query": "SELECT * FROM snapshots",
            "source": "b2-direct",
            "glob_path": "/srv/private/snapshots/*.parquet",
            "backend": "internal-storage",
            "rows": [[{"model": "private-model", "content": "public fact"}]],
        }

    monkeypatch.setattr(analytics, "run_snapshot_query", fake_query)
    result = await rag_tools.rag_query_snapshots(
        None,  # type: ignore[arg-type]
        {"query": "SELECT * FROM snapshots"},
    )
    assert isinstance(result, dict)
    assert result["rows"] == [[{"content": "public fact"}]]
    assert "glob_path" not in result
    assert "backend" not in result


@pytest.mark.asyncio
async def test_snapshot_query_error_does_not_echo_internal_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rugbrain.parquet_analytics as analytics

    def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("/srv/private/secret-provider failed")

    monkeypatch.setattr(analytics, "run_snapshot_query", fail)
    response = await rag_tools.rag_query_snapshots(
        None,  # type: ignore[arg-type]
        {"query": "SELECT * FROM snapshots"},
    )
    assert response.status_code == 502
    assert "secret-provider" not in response.body.decode()
    assert "snapshot query unavailable" in response.body.decode()


@pytest.mark.asyncio
async def test_chain_catalog_omits_storage_coordinates() -> None:
    result = await rag_tools.rag_chain_catalog(None)  # type: ignore[arg-type]
    assert result["data_source"] == "chain-data"
    assert "remote" not in result
    assert "prefix" not in result
    assert "b2-crypt" not in str(result)
