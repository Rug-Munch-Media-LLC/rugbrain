"""Tests for the generic pinned-repo documentation source (``repo_docs``)."""

from __future__ import annotations

import asyncio

import pytest

from rugbrain.ingest_sources import repo_docs
from rugbrain.ingest_sources.repo_docs import (
    RepoDocsSource,
    chunk_sections,
    classify_page_kind,
    detect_licence,
    split_sections,
)

SHA = "5f4f60df974d"
TREE = [
    {"path": "LICENSE", "size": 20},
    {"path": "docs/reference/api.md", "size": 40},
    {"path": "docs/guide/intro.mdx", "size": 40},
    {"path": "docs/legacy/old.adoc", "size": 40},
    {"path": "CHANGELOG.md", "size": 10},
    {"path": "i18n/es/readme.md", "size": 10},
]
FILES = {
    "LICENSE": "MIT License\n\nPermission is hereby granted, free of charge, to any person.",
    "docs/reference/api.md": "---\ntitle: API\nowner: docs\n---\n# API Reference\n\n`getFoo()` returns the foo object.\n\n| Field | Type |\n| --- | --- |\n| id | string |\n",
    "docs/guide/intro.mdx": "# Getting Started\n\nInstall it, then configure it.\n\n```bash\nnpm i thing\n```\n",
    "docs/legacy/old.adoc": "= Legacy\n\n== Usage\n\nOld style content kept for compatibility.\n",
    "CHANGELOG.md": "# 1.0.0\n\n- initial release\n",
    "i18n/es/readme.md": "# Hola\n\ncontenido\n",
}


def _patch(monkeypatch, tree=None):
    async def fake_list(client, repo, ref):
        return list(TREE if tree is None else tree)

    async def fake_raw(client, repo, ref, path):
        return FILES[path]

    monkeypatch.setattr(repo_docs, "list_repo_files", fake_list)
    monkeypatch.setattr(repo_docs, "fetch_raw", fake_raw)


def _run(src, limit=None):
    return asyncio.run(repo_docs.collect(None, spec=src, limit=limit))


def test_ref_required_and_floating_refs_rejected():
    with pytest.raises(ValueError, match="ref is required"):
        RepoDocsSource("acme/docs", "")
    for floating in ("main", "master", "HEAD"):
        with pytest.raises(ValueError, match="floats"):
            RepoDocsSource("acme/docs", floating)


def test_licence_missing_never_guesses(monkeypatch):
    _patch(monkeypatch, tree=[t for t in TREE if t["path"] != "LICENSE"])
    docs, stats = _run(RepoDocsSource("acme/docs", SHA))
    assert stats["licence"] == "" and stats["publishable"] is False
    assert docs and all(d.metadata["publishable"] is False for d in docs)


def test_licence_read_from_repo(monkeypatch):
    _patch(monkeypatch)
    docs, stats = _run(RepoDocsSource("acme/docs", SHA))
    assert stats["licence"] == "MIT" and stats["licence_path"] == "LICENSE"
    assert all(d.metadata["publishable"] is True for d in docs)
    assert detect_licence("") in ("", "UNKNOWN")


def test_exclude_globs_and_noise_dropped(monkeypatch):
    _patch(monkeypatch)
    docs, _ = _run(RepoDocsSource("acme/docs", SHA, exclude=("docs/legacy/*",)))
    paths = {d.metadata["path"] for d in docs}
    assert "docs/legacy/old.adoc" not in paths
    assert "CHANGELOG.md" not in paths and not any("i18n" in p for p in paths)


def test_adoc_is_ingested(monkeypatch):
    _patch(monkeypatch)
    docs, _ = _run(RepoDocsSource("acme/docs", SHA))
    adoc = [d for d in docs if d.metadata["path"] == "docs/legacy/old.adoc"]
    assert adoc and "Old style content" in adoc[0].content


def test_page_kind_classifier():
    assert classify_page_kind("docs/reference/api.md") == "reference"
    assert classify_page_kind("docs/guide/install.md") == "guide"
    assert classify_page_kind("README.md") == "landing"
    assert classify_page_kind("docs/misc/notes.md") == "other"


def test_heading_chunking_keeps_code_fences_intact():
    code = "```python\n" + "\n".join(f"print({i})" for i in range(400)) + "\n```\n"
    chunks = chunk_sections(split_sections(f"# Alpha\n\nintro\n\n{code}\n# Beta\n\nsecond\n"), limit=600)
    assert len(chunks) >= 2
    # never split inside a fence: every chunk carries a balanced fence count
    assert all(chunk.count("```") % 2 == 0 for _, chunk in chunks)
    joined = "".join(chunk for _, chunk in chunks)
    assert "print(0)" in joined and "print(399)" in joined


def test_three_file_fixture_end_to_end(monkeypatch):
    _patch(monkeypatch)
    docs, stats = _run(RepoDocsSource("acme/docs", SHA))
    assert stats["files"] == 3 and stats["failed"] == 0
    assert stats["by_page_kind"] == {"reference": 1, "guide": 1, "other": 1}
    assert len(docs) == stats["pages"] and docs
    keys = {"repo", "ref", "path", "section", "licence", "page_kind", "fetched_at",
            "publishable", "collection", "licence_path"}
    for doc in docs:
        assert keys <= set(doc.metadata)
        assert doc.metadata["repo"] == "acme/docs" and doc.metadata["ref"] == SHA
        assert doc.doc_id.startswith("repo_docs:acme/docs:5f4f60df974d:")
        assert doc.source == "repo_docs" and doc.doc_type == "repo_documentation"
    assert len({d.doc_id for d in docs}) == len(docs)


def test_max_pages_caps_output(monkeypatch):
    _patch(monkeypatch)
    docs, stats = _run(RepoDocsSource("acme/docs", SHA, max_pages=2))
    assert stats["pages"] == len(docs) == 2
