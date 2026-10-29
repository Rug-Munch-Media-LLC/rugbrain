"""writefix46 regression tests: ONE client contract + typed auth refusal."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rugbrain.ingest_sources import fetch_state, github, github_auth  # noqa: E402
from rugbrain.ingest_sources.github import list_repo_files  # noqa: E402
from rugbrain.qdrant_store.client_contract import (  # noqa: E402
    QdrantClientContractViolation,
    require_qdrant_http_client,
)
from rugbrain.qdrant_store.write_guard import slot_accepts  # noqa: E402


class FakeQdrantClient:
    """qdrant_client.AsyncQdrantClient shape: keyword API, no REST verbs."""

    async def get_collection(self, name: str):  # pragma: no cover
        raise AssertionError("REST boundary must reject this client before any call")


@pytest.mark.asyncio
async def test_slot_accepts_rejects_qdrant_client() -> None:
    with pytest.raises(QdrantClientContractViolation):
        await slot_accepts(FakeQdrantClient(), "rag_documents_8b", 4096)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_store_docs_rejects_wrong_client_before_any_write() -> None:
    from ingest_all_sources import _store_docs

    with pytest.raises(QdrantClientContractViolation):
        await _store_docs(  # type: ignore[arg-type]
            object(), FakeQdrantClient(), "src", "rag_documents_8b", [], False
        )


def test_httpx_async_client_is_the_contract() -> None:
    client = httpx.AsyncClient()
    assert require_qdrant_http_client(client, where="test") is client


async def _no_entries(repo: str, ref: str) -> list[dict]:
    return []


@pytest.mark.asyncio
async def test_missing_github_token_is_blocked_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN_FILE", raising=False)
    monkeypatch.setattr(github_auth, "DEFAULT_TOKEN_FILES", ())
    monkeypatch.setattr(github, "list_repo_files_git", _no_entries)
    fetch_state.clear_fetch_error()
    async with httpx.AsyncClient() as client:
        pages = await list_repo_files(client, "smartcontractkit/documentation", "5f4f60df974d")
    assert pages == []
    error = fetch_state.take_fetch_error()
    assert error is not None, "a missing token must record a fetch error, not pages=0"
    assert error.reason == "blocked.auth"
