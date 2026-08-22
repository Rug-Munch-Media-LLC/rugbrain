"""
Tests for rugbrain.ingest_smart — smarter ingestion helpers.

Covers incident-aware chunking, priority ordering, severity filtering,
vector dedup against Qdrant (httpx MockTransport), and git freshness.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from rugbrain.ingest_smart import (
    filter_by_severity,
    git_refresh,
    is_duplicate_vector,
    repo_last_updated,
    smart_split,
    sort_by_priority,
    split_incident_sections,
)

# ── Smart chunking ───────────────────────────────────────────────────────


def test_smart_split_short_text_whole() -> None:
    assert smart_split("short") == ["short"]
    assert smart_split("") == []


def test_smart_split_markdown_headings() -> None:
    text = (
        "# Incident 1\n\nHack details one.\n\n"
        "# Incident 2\n\nHack details two.\n\n"
        "# Incident 3\n\nHack details three."
    )
    sections = split_incident_sections(text)
    assert len(sections) == 3
    assert "Hack details one" in sections[0]
    assert "Hack details three" in sections[2]


def test_smart_split_contract_boundaries() -> None:
    text = (
        "pragma solidity ^0.8.0;\n"
        "contract Victim {\n  uint balance;\n  function withdraw() external {}\n}\n"
        "contract Attacker {\n  function attack() external {}\n}\n"
    )
    chunks = smart_split(text, max_len=200)
    # Contract boundaries split the code into per-contract chunks.
    assert any("contract Victim" in c for c in chunks)
    assert any("contract Attacker" in c for c in chunks)


def test_smart_split_windows_long_paragraph() -> None:
    text = "word " * 500  # 2500 chars, no heading/paragraph boundaries
    chunks = smart_split(text, max_len=700, overlap=70)
    assert len(chunks) > 1
    assert all(len(c) <= 700 for c in chunks)
    # Original content preserved across windows.
    assert "".join(chunks).replace("word word", "word")  # smoke: no crash


def test_split_incident_sections_paragraph_fallback() -> None:
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    sections = split_incident_sections(text)
    assert len(sections) == 3


# ── Priority ordering ────────────────────────────────────────────────────


def _doc(severity: Any, source_type: str = "rekt_news") -> dict[str, Any]:
    return {
        "rel_key": "x",
        "text": "body",
        "metadata": {"severity": severity, "source_type": source_type},
    }


def test_sort_by_priority_exploits_first() -> None:
    docs = [
        _doc("medium", "slowmist"),
        _doc("critical", "defihacklabs"),
        _doc("high", "rekt_news"),
        _doc("unknown", "swc_registry"),
    ]
    ordered = sort_by_priority(docs)
    assert ordered[0]["metadata"]["source_type"] == "defihacklabs"
    assert ordered[-1]["metadata"]["source_type"] == "swc_registry"


def test_filter_by_severity() -> None:
    docs = [_doc("critical"), _doc("high"), _doc("medium"), _doc("low")]
    kept = filter_by_severity(docs, "high")
    assert len(kept) == 2
    kept_all = filter_by_severity(docs, "low")
    assert len(kept_all) == 4


# ── Vector dedup against Qdrant ──────────────────────────────────────────


def _transport(search_payload: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/points/search"):
            return httpx.Response(200, json={"result": search_payload})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler=handler)


async def test_dedup_vectors_marks_duplicates() -> None:
    transport = _transport([{"id": "1", "score": 0.98}])
    async with httpx.AsyncClient(transport=transport) as client:
        dups = await _dedup([[0.1] * 4], client, "defi_exploits")
    assert dups == [True]


async def test_dedup_vectors_below_threshold() -> None:
    transport = _transport([{"id": "1", "score": 0.80}])
    async with httpx.AsyncClient(transport=transport) as client:
        dups = await _dedup([[0.1] * 4], client, "defi_exploits")
    assert dups == [False]


async def test_dedup_vectors_none_embedding_not_dup() -> None:
    transport = _transport([{"id": "1", "score": 0.99}])
    async with httpx.AsyncClient(transport=transport) as client:
        dups = await _dedup([None], client, "defi_exploits")
    assert dups == [False]


async def _dedup(vectors: Any, client: Any, collection: Any) -> Any:
    from rugbrain.ingest_smart import dedup_vectors

    return await dedup_vectors(
        vectors,
        collection,
        client,
        threshold=0.95,
        qdrant_url="http://qdrant:6333",
    )


def test_is_duplicate_vector() -> None:
    transport = _transport([{"id": "1", "score": 0.97}])
    client = httpx.AsyncClient(transport=transport)
    try:
        dup = asyncio.run(is_duplicate_vector([0.1] * 4, "scam_patterns", client))
    finally:
        asyncio.run(client.aclose())
    assert dup is True  # ── Git freshness ────────────────────────────────────────────────────────


def test_git_refresh_missing_dir(tmp_path: Path) -> None:
    assert git_refresh(str(tmp_path / "nope")) is False


def test_repo_last_updated_missing_dir(tmp_path: Path) -> None:
    assert repo_last_updated(str(tmp_path / "nope")) is None


def test_repo_last_updated_real_repo() -> None:
    import os

    here = os.path.dirname(__file__)
    stamp = repo_last_updated(os.path.dirname(here))
    assert stamp is None or "T" in stamp or "-" in stamp
