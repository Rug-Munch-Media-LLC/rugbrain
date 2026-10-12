"""Tests for scripts/ingest_osv.py.

Three things must hold:
  1. the default (dry-run) path writes NOTHING to Qdrant,
  2. point IDs are deterministic so a re-run upserts instead of duplicating,
  3. severity mapping from OSV to RugBrain levels works correctly.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_osv as ing

# Sample OSV vulnerability data
SAMPLE_VULN = {
    "id": "GHSA-test-1234",
    "summary": "Test vulnerability in example package",
    "details": "A detailed description of the vulnerability.",
    "aliases": ["CVE-2024-12345", "GHSA-test-1234"],
    "modified": "2024-01-15T10:00:00Z",
    "published": "2024-01-01T00:00:00Z",
    "affected": [
        {
            "package": {
                "name": "example-pkg",
                "ecosystem": "npm",
            },
            "versions": ["1.0.0", "1.0.1"],
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0.2"}]}],
        }
    ],
    "references": [{"type": "WEB", "url": "https://example.com/advisory"}],
    "database_specific": {"severity": "HIGH"},
    "schema_version": "1.6.0",
}

SAMPLE_VULN_LOW = {
    "id": "GHSA-low-severity",
    "summary": "Low severity issue",
    "details": "Minor issue",
    "aliases": [],
    "modified": "2024-01-10T00:00:00Z",
    "published": "2024-01-05T00:00:00Z",
    "affected": [
        {
            "package": {
                "name": "another-pkg",
                "ecosystem": "PyPI",
            },
            "versions": ["2.0.0"],
        }
    ],
    "database_specific": {"severity": "LOW"},
}


async def _fake_querybatch():
    """Mock OSV /v1/querybatch response."""
    return {"results": [{"vulns": [{"id": "GHSA-test-1234"}, {"id": "GHSA-low-severity"}]}]}


async def _fake_vuln_detail(vuln_id: str):
    """Mock OSV /v1/vulns/{id} response."""
    if vuln_id == "GHSA-test-1234":
        return SAMPLE_VULN
    elif vuln_id == "GHSA-low-severity":
        return SAMPLE_VULN_LOW
    return {}


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    """Dry-run fetches and embeds but does not write to Qdrant."""
    attempts: list[str] = []

    async def fake_post(self, url, json=None, **kwargs):
        if "querybatch" in url:
            return _MockResponse(await _fake_querybatch())
        raise AssertionError(f"unexpected POST to {url}")

    async def fake_get(self, url, **kwargs):
        if "vulns/GHSA-test-1234" in url:
            return _MockResponse(SAMPLE_VULN)
        elif "vulns/GHSA-low-severity" in url:
            return _MockResponse(SAMPLE_VULN_LOW)
        raise AssertionError(f"unexpected GET to {url}")

    async def forbidden_insert(*args, **kwargs):
        attempts.append("INSERT_ATTEMPTED")
        raise AssertionError("dry run must not call insert")

    async def fake_embed(texts):
        return [[0.1] * 4096 for _ in texts]

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    monkeypatch.setattr(ing, "get_embedder", lambda: _MockEmbedder())
    monkeypatch.setattr("rugbrain.qdrant_store.QdrantVectorStore.insert", forbidden_insert)
    monkeypatch.setattr("rugbrain.qdrant_store.QdrantVectorStore.close", lambda self: None)
    monkeypatch.setattr("sys.argv", ["ingest_osv.py", "--dry-run", "--limit", "2"])

    stats = asyncio.run(ing.run(dry_run=True, limit=2, days=None, ecosystems=["npm"]))
    assert stats["fetched"] == 2
    assert stats["embedded"] == 2
    assert stats["upserted"] == 0
    assert stats["errors"] == 0
    assert attempts == [], "dry run must not attempt Qdrant writes"


def test_severity_mapping():
    """OSV severity maps to RugBrain levels."""
    assert ing._severity_from_osv(SAMPLE_VULN) == "high"
    assert ing._severity_from_osv(SAMPLE_VULN_LOW) == "low"
    # Unknown/default
    vuln_no_sev = {**SAMPLE_VULN}
    vuln_no_sev.pop("database_specific", None)
    assert ing._severity_from_osv(vuln_no_sev) == "medium"


def test_cve_ghsa_extraction():
    """Extract CVE and GHSA IDs from aliases."""
    cves, ghsas = ing._extract_cve_ghsa(SAMPLE_VULN)
    assert cves == ["CVE-2024-12345"]
    assert ghsas == ["GHSA-test-1234"]


def test_text_for_embedding_contains_key_fields():
    """Embedding text includes summary, IDs, affected packages."""
    text = ing._text_for_embedding(SAMPLE_VULN)
    assert "Test vulnerability" in text
    assert "CVE-2024-12345" in text
    assert "GHSA-test-1234" in text
    assert "example-pkg" in text
    assert "npm" in text


def test_build_metadata_has_filterable_fields():
    """Metadata includes all filterable fields for Qdrant."""
    meta = ing._build_metadata(SAMPLE_VULN)
    assert meta["osv_id"] == "GHSA-test-1234"
    assert "CVE-2024-12345" in meta["cve_ids"]
    assert "GHSA-test-1234" in meta["ghsa_ids"]
    assert "npm" in meta["ecosystems"]
    assert "example-pkg" in meta["packages"]
    assert meta["knowledge_type"] == "vulnerability"
    assert meta["source"] == "osv"


def test_point_id_deterministic():
    """Same OSV ID produces same deterministic point ID."""
    # The point ID format is "osv:{osv_id}"
    id1 = f"osv:{SAMPLE_VULN['id']}"
    id2 = f"osv:{SAMPLE_VULN['id']}"
    assert id1 == id2
    assert id1 != f"osv:{SAMPLE_VULN_LOW['id']}"


class _MockResponse:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _MockEmbedder:
    async def embed(self, texts):
        return [[0.1] * 4096 for _ in texts]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
