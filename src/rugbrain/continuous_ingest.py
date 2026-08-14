"""RugBrain — continuous ingestion loop (quality-over-time layer).

Iterates the registered sources and, per source: checks freshness, fetches
deltas, scores quality, dedups, filters low-quality content, and ingests —
idempotently (deterministic Qdrant point IDs make re-runs overwrite the
same points). ``dry_run=True`` fetches and scores but never writes.

This is NOT a scheduler daemon: cadence/tick logic lives in
``scripts/ingest_continuous.py``. This module is the ingestion layer that
makes each pass *quality-aware*: only stale sources are re-fetched, every
doc carries a ``quality_score`` in metadata, duplicates are dropped across
sources, and low-quality docs can be filtered before they reach the store.

Usage::

    registry = FreshnessRegistry(path="data/freshness.json")
    sources = [
        ContinuousSource(
            name="slowmist",
            collection="defi_exploits",
            fetch=lambda: fetch_slowmist(),
            version=lambda: git_head_version("/srv/repos/slowmist"),
            max_age_seconds=6 * 3600,
        ),
    ]
    summaries = await run_continuous_ingest(sources, registry, dry_run=True)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from rugbrain.freshness_registry import FreshnessRegistry
from rugbrain.ingest_common import CrawlDocument, Embedder, VectorStore, store_documents
from rugbrain.ingestion_quality import (
    DEFAULT_MIN_QUALITY,
    filter_low_quality,
    tag_documents,
)

log = logging.getLogger(__name__)

FetchFn = Callable[[], Awaitable[Sequence[CrawlDocument]]]
VersionFn = Callable[[], str | None]


@dataclass(frozen=True)
class ContinuousSource:
    """Declarative config for one continuous-ingestion source.

    ``fetch`` returns the current documents for the source; ``version`` is
    an optional way to detect upstream change (e.g. a git HEAD version for
    GitHub-backed sources, or a feed ETag). ``max_age_seconds`` controls
    periodic re-ingest for sources without a usable version.
    """

    name: str
    collection: str
    fetch: FetchFn
    version: VersionFn | str | None = None
    max_age_seconds: int = 6 * 3600
    min_quality: int = DEFAULT_MIN_QUALITY


def _resolve_version(source: ContinuousSource) -> str | None:
    """Evaluate a source's version provider defensively (never raise)."""
    version = source.version
    if version is None or isinstance(version, str):
        return version
    try:
        return version()
    except Exception as e:
        log.warning("continuous_version_failed source=%s err=%s", source.name, str(e)[:120])
        return None


async def run_continuous_ingest(
    sources: Sequence[ContinuousSource],
    registry: FreshnessRegistry,
    *,
    dry_run: bool = False,
    store: VectorStore | None = None,
    embedder: Embedder | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Run one quality-aware ingestion pass over *sources*.

    Per source:
      1. skip when fresh (``registry.should_reingest``)
      2. fetch current documents
      3. score + tag quality metadata, dedup by content fingerprint
         (shared across sources in the same pass)
      4. filter docs below ``source.min_quality``
      5. ingest via ``store_documents`` — or count-only when dry-run or no
         store/embedder are provided
      6. record the pass in the registry (failures do not advance freshness)

    Returns ``{source_name: summary_dict}``. Idempotent: re-running with the
    same inputs produces the same store state (deterministic point IDs).
    """
    summaries: dict[str, Any] = {}
    seen_fingerprints: set[str] = set()

    for source in sources:
        version = _resolve_version(source)
        if not registry.should_reingest(
            source.name,
            max_age_seconds=source.max_age_seconds,
            current_version=version,
            now=now,
        ):
            log.debug("continuous_skip source=%s reason=fresh", source.name)
            summaries[source.name] = {"skipped": True, "reason": "fresh"}
            continue

        log.info("continuous_start source=%s dry_run=%s", source.name, dry_run)
        try:
            fetched = list(await source.fetch())
        except Exception as e:
            log.warning("continuous_fetch_failed source=%s err=%s", source.name, str(e)[:200])
            registry.mark_error(source.name, str(e)[:200])
            summaries[source.name] = {"ok": False, "error": str(e)[:200]}
            continue

        if not fetched:
            # Successful pass with nothing new — record so we don't hammer
            # the source every loop.
            registry.record(
                source.name,
                collection=source.collection,
                version=version,
                count=0,
                now=now,
            )
            summaries[source.name] = {"ok": True, "fetched": 0, "empty": True}
            continue

        batch = tag_documents(fetched, seen_fingerprints=seen_fingerprints)
        seen_fingerprints = batch.seen_fingerprints
        kept, dropped = filter_low_quality(batch.docs, min_score=source.min_quality)

        store_result: dict[str, Any] | None = None
        if dry_run or store is None or embedder is None:
            ingested = len(kept)
        else:
            store_result = await store_documents(kept, embedder, store)
            ingested = int(store_result.get("inserted", 0))

        registry.record(
            source.name,
            collection=source.collection,
            version=version,
            count=ingested,
            now=now,
        )
        summaries[source.name] = {
            "ok": True,
            "version": version,
            "fetched": len(fetched),
            "duplicates": batch.duplicate_count,
            "low_quality_dropped": len(dropped),
            "ingested": ingested,
            "store": store_result,
        }
        log.info(
            "continuous_done source=%s %s",
            source.name,
            {k: v for k, v in summaries[source.name].items() if k != "store"},
        )
    return summaries


__all__ = [
    "ContinuousSource",
    "FetchFn",
    "VersionFn",
    "run_continuous_ingest",
]
