"""Tests for the continuous-ingestion quality layer.

Covers the three new modules:

  * ``rugbrain.ingestion_quality``  — quality scoring, tagging, dedup,
    filtering, ranking
  * ``rugbrain.freshness_registry`` — ingest tracking + stale detection
  * ``rugbrain.continuous_ingest``  — the idempotent, dry-run-able loop

Also serves as the smoke path: quality scoring + dedup + loop run
in-process with no external services.
"""

from __future__ import annotations

import time
from pathlib import Path

from rugbrain.continuous_ingest import ContinuousSource, run_continuous_ingest
from rugbrain.freshness_registry import FreshnessRegistry, git_head_sha, git_head_version
from rugbrain.ingest_common import CrawlDocument, doc_from_article
from rugbrain.ingestion_quality import (
    filter_low_quality,
    rank_by_quality,
    score_chunk,
    score_document,
    tag_documents,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── Quality scoring ─────────────────────────────────────────────────────


def test_score_chunk_axes_and_range():
    score = score_chunk("Hackers drained $12.4m from 0x" + "a" * 40 + " via reentrancy exploit.")
    assert 0 <= score.composite <= 100
    assert set(score.axes) == {
        "length",
        "structure",
        "source_authority",
        "uniqueness",
        "crypto_signals",
    }
    assert score.tier in {"excellent", "good", "fair", "poor"}


def test_length_penalty_short_content():
    assert score_chunk("").composite < 40
    assert score_chunk("").axes["length"] == 0
    assert score_chunk("short").axes["length"] < 40


def test_long_structured_content_scores_high():
    text = "\n\n".join(
        f"# Section {i}\n\n" + "Detailed analysis of the exploit. " * 20 for i in range(4)
    )
    score = score_chunk(text, source="coindesk", url="https://coindesk.com/post")
    assert score.axes["length"] >= 85
    assert score.axes["source_authority"] >= 80
    assert score.composite >= 60


def test_duplicate_penalizes_uniqueness():
    fresh = score_chunk("unique content here")
    dup = score_chunk("unique content here", is_duplicate=True)
    assert fresh.axes["uniqueness"] == 100
    assert dup.axes["uniqueness"] < 50
    assert dup.is_duplicate is True
    assert fresh.composite > dup.composite


def test_crypto_signals_detect_artifacts():
    rich = "Attacker stole 5000 ETH ($12.4m) from 0x" + "b" * 40 + " in a flash loan exploit."
    plain = "The protocol released a new version with several improvements."
    assert score_chunk(rich).axes["crypto_signals"] > score_chunk(plain).axes["crypto_signals"]


def test_source_authority_unknown_low():
    score = score_chunk("Some analysis text. " * 10, source="mysteryblog")
    assert score.axes["source_authority"] < 50
    assert "low_authority_source" in score.signals["source_authority"]


# ── Doc tagging + dedup ─────────────────────────────────────────────────


def _doc(content: str, url: str = "https://x/1", source: str = "s") -> CrawlDocument:
    return doc_from_article(title="T", content=content, url=url, source=source, collection="c")


def test_tag_documents_metadata_and_dedup():
    docs = [_doc("Alpha content " * 10), _doc("Alpha content " * 10), _doc("Beta content " * 10)]
    batch = tag_documents(docs)
    assert batch.duplicate_count == 1
    assert "quality_score" in batch.docs[0].metadata
    assert batch.docs[1].metadata["is_duplicate"] is True
    assert batch.docs[0].metadata["quality_score"] > batch.docs[1].metadata["quality_score"]


def test_tag_documents_shares_seen_across_calls():
    docs1 = [_doc("Same body " * 10)]
    docs2 = [_doc("Same body " * 10)]
    b1 = tag_documents(docs1)
    b2 = tag_documents(docs2, seen_fingerprints=b1.seen_fingerprints)
    assert b2.duplicate_count == 1


def test_score_document_uses_full_content():
    doc = _doc("Signal-rich: 0x" + "c" * 40 + " drained via reentrancy. " * 10, source="coindesk")
    score = score_document(doc)
    assert 0 <= score.composite <= 100
    assert "quality_score" not in doc.metadata  # score_document does not tag


def test_filter_and_rank():
    docs = [
        _doc("good " * 200, url="https://coindesk.com/a", source="coindesk"),
        _doc("tiny", url="https://unknown.example/b", source="mysteryblog"),
    ]
    tag_documents(docs)
    kept, dropped = filter_low_quality(docs, min_score=50)
    assert dropped == [docs[1]]
    assert kept == [docs[0]]
    ranked = rank_by_quality(docs)
    assert ranked[0] is docs[0]


def test_filter_keeps_untagged():
    docs = [_doc("untagged " * 20)]
    kept, dropped = filter_low_quality(docs, min_score=90)
    assert kept == [docs[0]]
    assert dropped == []


# ── Freshness registry ──────────────────────────────────────────────────


def test_registry_never_ingested_is_stale():
    reg = FreshnessRegistry()
    assert reg.should_reingest("news", max_age_seconds=3600) is True


def test_registry_record_and_fresh():
    reg = FreshnessRegistry()
    reg.record("news", version="v1", count=5)
    assert reg.get("news") is not None
    assert (
        reg.should_reingest(
            "news", max_age_seconds=3600, current_version="v1", now=time.time() + 10
        )
        is False
    )


def test_registry_version_change_triggers_reingest():
    reg = FreshnessRegistry()
    reg.record("github", version="sha-old")
    assert (
        reg.should_reingest(
            "github", max_age_seconds=86400, current_version="sha-new", now=time.time() + 10
        )
        is True
    )


def test_registry_age_expiry():
    reg = FreshnessRegistry()
    reg.record("news", version="v1", now=1000.0)
    assert (
        reg.should_reingest("news", max_age_seconds=60, current_version="v1", now=1040.0) is False
    )
    assert reg.should_reingest("news", max_age_seconds=60, current_version="v1", now=1061.0) is True


def test_registry_error_retries():
    reg = FreshnessRegistry()
    reg.record("news", version="v1", now=1000.0)
    reg.mark_error("news", "boom")
    assert (
        reg.should_reingest("news", max_age_seconds=3600, current_version="v1", now=1001.0) is True
    )
    assert reg.get("news") is not None
    assert reg.get("news").last_status == "error"  # type: ignore[union-attr]


def test_registry_json_persistence(tmp_path: Path):
    path = tmp_path / "registry.json"
    reg = FreshnessRegistry(path=path)
    reg.record("github", version="sha1", count=3)
    reg2 = FreshnessRegistry(path=path)  # auto-loads on init
    rec = reg2.get("github")
    assert rec is not None
    assert rec.version == "sha1"
    assert rec.last_count == 3


def test_stale_sources_filter():
    reg = FreshnessRegistry()
    reg.record("a", version="v1", now=1000.0)
    reg.record("b", version="v1", now=1000.0)
    stale = reg.stale_sources(
        ["a", "b", "c"],
        max_age_seconds=60,
        versions={"a": "v1", "b": "v2", "c": None},
        now=1010.0,
    )
    assert set(stale) == {"b", "c"}


def test_git_head_version_repo():
    sha = git_head_sha(str(REPO_ROOT))
    assert sha is not None
    assert len(sha) == 40
    version = git_head_version(str(REPO_ROOT))
    assert version is not None
    assert version.startswith(sha)


def test_git_head_version_missing_dir():
    assert git_head_sha(str(REPO_ROOT / "does-not-exist")) is None
    assert git_head_version(str(REPO_ROOT / "does-not-exist")) is None


# ── Continuous loop ─────────────────────────────────────────────────────


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed_one(self, text: str) -> list[float]:
        self.calls += 1
        return [0.1] * 1024


class _FakeStore:
    def __init__(self) -> None:
        self.points: list[tuple[str, str]] = []

    async def insert(
        self,
        doc_id,
        collection,
        embedding,
        content="",
        metadata=None,
        source="",
        severity="medium",
        chain="",
    ):
        self.points.append((doc_id, content))
        return True


def _source(name: str, docs: list[CrawlDocument], version: str | None = None) -> ContinuousSource:
    async def fetch() -> list[CrawlDocument]:
        return docs

    return ContinuousSource(
        name=name,
        collection="defi_exploits",
        fetch=fetch,
        version=version,
        max_age_seconds=60,
    )


async def test_loop_dry_run_scores_and_records():
    reg = FreshnessRegistry()
    docs = [_doc("A real incident: 0x" + "d" * 40 + " drained $5m. " * 5)]
    result = await run_continuous_ingest([_source("slowmist", docs)], reg, dry_run=True)
    summary = result["slowmist"]
    assert summary["ok"] is True
    assert summary["fetched"] == 1
    assert summary["duplicates"] == 0
    assert summary["ingested"] == 1  # count-only in dry-run
    assert summary["store"] is None
    rec = reg.get("slowmist")
    assert rec is not None
    assert rec.last_count == 1


async def test_loop_skips_fresh_source():
    reg = FreshnessRegistry()
    reg.record("news", version="v1", count=2, now=time.time())
    result = await run_continuous_ingest(
        [_source("news", [_doc("body " * 20)], version="v1")],
        reg,
        dry_run=True,
        now=time.time() + 10,
    )
    assert result["news"] == {"skipped": True, "reason": "fresh"}


async def test_loop_version_change_reingests():
    reg = FreshnessRegistry()
    reg.record("github", version="old", count=1, now=time.time())
    docs = [_doc("New upstream content " * 10)]
    result = await run_continuous_ingest(
        [_source("github", docs, version="new")],
        reg,
        dry_run=True,
        now=time.time() + 10,
    )
    assert result["github"]["ok"] is True
    assert result["github"]["version"] == "new"


async def test_loop_dedup_across_sources():
    reg = FreshnessRegistry()
    shared = _doc("Identical post body " * 10)
    sources = [
        _source("src_a", [shared]),
        _source(
            "src_b",
            [
                doc_from_article(
                    title="T",
                    content="Identical post body " * 10,
                    url="https://x/2",
                    source="b",
                    collection="c",
                )
            ],
        ),
    ]
    result = await run_continuous_ingest(sources, reg, dry_run=True)
    assert result["src_a"]["duplicates"] == 0
    assert result["src_b"]["duplicates"] == 1
    assert result["src_b"]["ingested"] == 0  # duplicate-only batch is filtered


async def test_loop_fetch_error_does_not_advance_freshness():
    reg = FreshnessRegistry()
    reg.record("boom", version="v1", count=1, now=1000.0)

    async def fetch() -> list[CrawlDocument]:
        raise RuntimeError("network down")

    source = ContinuousSource(name="boom", collection="c", fetch=fetch, max_age_seconds=60)
    result = await run_continuous_ingest([source], reg, dry_run=True, now=1061.0)
    assert result["boom"]["ok"] is False
    rec = reg.get("boom")
    assert rec is not None
    assert rec.last_status == "error"
    assert rec.last_ingested_at == 1000.0  # not advanced
    assert (
        reg.should_reingest("boom", max_age_seconds=3600, current_version="v1", now=1061.0) is True
    )


async def test_loop_stores_when_store_provided():
    reg = FreshnessRegistry()
    docs = [_doc("storable content " * 20)]
    store = _FakeStore()
    embedder = _FakeEmbedder()
    result = await run_continuous_ingest(
        [_source("with_store", docs)],
        reg,
        dry_run=False,
        store=store,  # type: ignore[arg-type]
        embedder=embedder,
    )
    assert result["with_store"]["ok"] is True
    assert result["with_store"]["store"] is not None
    assert result["with_store"]["store"]["inserted"] >= 1
    assert len(store.points) >= 1
    assert reg.get("with_store").last_count == result["with_store"]["ingested"]  # type: ignore[union-attr]
