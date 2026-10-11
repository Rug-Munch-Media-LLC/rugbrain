"""Unit tests for ingest_contract_audits sources + 8B lane routing.

Covers the five verification points:
  1. ``parse_audit_table`` → structured rows.
  2. ``Qwen3Embedder.DIMS == 4096`` (canonical 8B lane, never 2560/4B).
  3. UUIDv5 ``stable_id`` determinism (idempotent upserts).
  4. ``contract_audits`` → ``contract_audits_8b`` lane routing.
  5. All three sources import + dispatch with correct names.

No network: source fetching is mocked; embedder/lane checks are class/import-level.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch


# ── 1. parse_audit_table → rows ────────────────────────────────────────
def test_parse_audit_table_returns_rows() -> None:
    from rugbrain.ingest_sources.openzeppelin_audits import parse_audit_table

    sample = (
        "| Date | Version | Commit | Auditor | Scope | Links |\n"
        "|---|---|---|---|---|---|\n"
        "| 2024-10-01 | v5.1 | abc123 | OpenZeppelin | AccessControl | [r](audits/x.pdf) |\n"
        "| 2025-04-01 | v5.3 | def456 | OpenZeppelin | ERC4626 | [r](audits/y.pdf) |\n"
    )
    rows = parse_audit_table(sample)
    assert len(rows) == 2
    assert rows[0]["date"] == "2024-10-01"
    assert rows[0]["version"] == "v5.1"
    assert rows[0]["commit"] == "abc123"
    assert rows[0]["auditor"] == "OpenZeppelin"
    assert rows[0]["scope"] == "AccessControl"
    assert rows[1]["date"] == "2025-04-01"
    assert rows[1]["scope"] == "ERC4626"


def test_parse_audit_table_empty() -> None:
    from rugbrain.ingest_sources.openzeppelin_audits import parse_audit_table

    assert parse_audit_table("") == []
    assert parse_audit_table(None) == []


# ── 2. Qwen3Embedder.DIMS == 4096 ──────────────────────────────────────
def test_qwen3_embedder_dims_4096() -> None:
    from rugbrain.qwen_embedders import Qwen3Embedder

    assert Qwen3Embedder.DIMS == 4096
    assert Qwen3Embedder.MODEL == "qwen3-embedding-8b"


# ── 3. UUIDv5 stable_id determinism ────────────────────────────────────
def test_stable_id_uuidv5_deterministic() -> None:
    from rugbrain.ingest_common import CrawlDocument, content_hash

    doc = CrawlDocument(
        title="T",
        content="C",
        url="https://x/a",
        source="openzeppelin_audits",
        collection="contract_audits",
    )
    ch = content_hash("chunk-body")
    id_a = doc.stable_id(ch)
    id_b = doc.stable_id(ch)
    assert id_a == id_b  # identical → idempotent upsert
    parsed = uuid.UUID(id_a)
    assert parsed.version == 5  # UUIDv5
    assert str(parsed) == id_a
    assert doc.stable_id(ch) != doc.stable_id(content_hash("different"))


def test_stable_id_matches_uuid5_seed() -> None:
    from rugbrain.ingest_common import CrawlDocument, content_hash

    doc = CrawlDocument(
        title="T",
        content="C",
        url="https://x/a",
        source="s",
        collection="c",
    )
    ch = content_hash("text")
    seed = f"s:https://x/a:{ch}"
    expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))
    assert doc.stable_id(ch) == expected


# ── 4. contract_audits → contract_audits_8b routing ──────────────────
def test_contract_audits_routes_to_8b() -> None:
    from rugbrain.qdrant_store.lane_resolver import (
        CANONICAL_LANE_DIM,
        resolve_collection,
    )

    assert resolve_collection("contract_audits") == "contract_audits_8b"
    assert resolve_collection("contract_audits_8b") == "contract_audits_8b"
    assert CANONICAL_LANE_DIM == 4096


# ── 5. All three sources importable with correct names ────────────────
def test_all_three_sources_importable() -> None:
    from rugbrain.ingest_sources.openzeppelin_audits import OpenZeppelinAuditsSource
    from rugbrain.ingest_sources.spearbit_content import SpearbitContentSource
    from rugbrain.ingest_sources.trailofbits_publications import (
        TrailOfBitsPublicationsSource,
    )

    assert OpenZeppelinAuditsSource.name == "openzeppelin_audits"
    assert SpearbitContentSource.name == "spearbit_content"
    assert TrailOfBitsPublicationsSource.name == "trailofbits_publications"


# ── 6. list_repo_files falls back to git clone on 403 ─────────────────
async def test_list_repo_files_falls_back_to_git() -> None:
    from rugbrain.ingest_sources.github import list_repo_files

    client = AsyncMock()
    client.get = AsyncMock(
        return_value=AsyncMock(status_code=403, headers={}, text="", json=lambda: {})
    )
    fake = [{"path": "audits/README.md", "size": 1}]
    with patch(
        "rugbrain.ingest_sources.github.list_repo_files_git",
        new_callable=AsyncMock,
        return_value=fake,
    ) as mock_git:
        result = await list_repo_files(client, "test/repo", "main")
    assert result == fake
    mock_git.assert_awaited_once_with("test/repo", "main")
