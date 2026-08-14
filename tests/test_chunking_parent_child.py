"""Parent-child document chunking tests for ``rugbrain.app_chunking``.

Covers ``split_doc_parent_child``: small search-oriented child chunks
(~512 tokens, heading + metadata) that each reference a larger parent
context window (full section, or a 2-4k-token slice when oversized).

The parent-child API is purely additive — the existing
``split_doc_into_chunks`` / ``chunk_document`` APIs are unchanged.

All tests are unit tests: no external services required.
"""

from __future__ import annotations

import re

from rugbrain.app_chunking import (
    ParentChunk,
    split_doc_into_chunks,
    split_doc_parent_child,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _doc(paragraphs: int = 10, per_para: int = 30) -> str:
    """A markdown doc with one section and *paragraphs* repeated paragraphs."""
    body = "\n\n".join(
        ("The contract at 0xabc shows honeypot signals and locked liquidity. " * per_para)
        for _ in range(paragraphs)
    )
    return f"# Audit Report\n\n{body}"


# ── structure ──


def test_parent_child_structure():
    parents = split_doc_parent_child(_doc())
    assert parents
    parent = parents[0]
    assert isinstance(parent, ParentChunk)
    assert isinstance(parent.content, str) and parent.content
    assert isinstance(parent.child_chunks, list) and parent.child_chunks
    assert isinstance(parent.metadata, dict)
    assert parent.metadata["parent_id"]
    assert parent.metadata["section"] == "Audit Report"
    assert parent.metadata["doc_type"] == "default"


def test_empty_input_returns_empty_list():
    assert split_doc_parent_child("") == []
    assert split_doc_parent_child("   \n  ") == []


# ── child → parent reference ──


def test_children_reference_their_parent():
    parents = split_doc_parent_child(_doc())
    for parent in parents:
        assert parent.child_chunks, "parent without children"
        for child in parent.child_chunks:
            assert child["parent_id"] == parent.metadata["parent_id"], (
                "child must reference its own parent"
            )
            assert child["parent_index"] == parent.metadata["parent_index"]
            assert child["parent_total"] == parent.metadata["parent_total"]
            assert 0 <= child["parent_index"] < child["parent_total"]


def test_children_carry_heading_and_metadata():
    parents = split_doc_parent_child(
        _doc(), "scam_report", doc_meta={"source": "auditor", "title": "RugVault audit"}
    )
    child = parents[0].child_chunks[0]
    assert child["text"].split("\n", 1)[0] == "# Audit Report"
    assert child["doc_type"] == "scam_report"
    assert child["source"] == "auditor"
    assert child["title"] == "RugVault audit"
    assert child["estimated_tokens"] >= 1


def test_children_are_subdivisions_of_parent():
    parents = split_doc_parent_child(_doc())
    for parent in parents:
        parent_core = (
            _norm(parent.content.split("\n", 1)[1])
            if "\n" in parent.content
            else _norm(parent.content)
        )
        for child in parent.child_chunks:
            head, _, core = child["text"].partition("\n")
            assert head == parent.content.split("\n", 1)[0].strip()
            if core.strip():
                assert _norm(core) in parent_core, "child core must live inside its parent"


# ── oversized sections split into parent windows ──


def test_oversized_section_splits_into_parent_windows():
    parents = split_doc_parent_child(
        _doc(paragraphs=30, per_para=40), parent_token_target=200, parent_max_chars=1500
    )
    assert len(parents) > 1, "oversized section should produce multiple parents"
    for parent in parents:
        assert len(parent.content) <= 1500, "parent window exceeded the hard cap"
        assert parent.metadata["parent_total"] == len(parents)
    assert [p.metadata["parent_index"] for p in parents] == list(range(len(parents)))
    # every child points at exactly one parent and that parent is in range
    for parent in parents:
        for child in parent.child_chunks:
            assert child["parent_id"] == parent.metadata["parent_id"]


def test_multiple_sections_produce_parents_per_section():
    doc = "# Section One\n\n" + ("alpha " * 300) + "\n\n# Section Two\n\n" + ("beta " * 300)
    parents = split_doc_parent_child(doc)
    sections = {p.metadata["section"] for p in parents}
    assert "Section One" in sections
    assert "Section Two" in sections
    for parent in parents:
        assert parent.content.startswith("# ")


# ── additive guarantee ──


def test_existing_api_unchanged():
    doc = _doc(paragraphs=4, per_para=10)
    flat = split_doc_into_chunks(doc, "default")
    assert flat
    assert all("parent_id" not in c for c in flat), "flat chunks must not gain parent fields"
    parents = split_doc_parent_child(doc)
    assert parents
    # same underlying sectioning/strategy is reused
    assert parents[0].metadata["section"] == flat[0]["section"]
