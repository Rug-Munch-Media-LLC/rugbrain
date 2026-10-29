"""Dedup → embed → store, through the news channel's own pipeline.

There is one embedding path in this repo and this module does not add a second:
items go to :class:`rugbrain.firehose.pipeline.IngestionPipeline`, which computes
the content hash, embeds with the canonical 4096-d Qwen3-Embedding-8B embedder
and upserts into Qdrant. The only new step is the cross-source collapse that
happens *before* the pipeline sees the batch, so a syndicated story is embedded
once and carries all its citations.
"""

from __future__ import annotations

from typing import Any

from rugbrain.ingest_sources.content_sources.collapse import collapse_items

__all__ = ["ingest_items"]


def _prepare(item: dict[str, Any], collection: str) -> tuple[str, str, dict[str, Any]]:
    """(target collection, content, metadata) for one canonical item."""
    metadata = dict(item.get("metadata") or {})
    metadata.setdefault("citations", [])
    return str(item.get("collection") or collection), str(item.get("content") or ""), metadata


def _record(report: dict[str, Any], result: dict[str, Any]) -> None:
    """Fold one pipeline result into the report counts."""
    status = str(result.get("status") or "")
    if status == "ingested":
        report["ingested"] += 1
        report["ids"].append(result.get("id"))
    elif status == "duplicate":
        report["duplicates"] += 1
    else:
        report["skipped"] += 1


async def ingest_items(
    items: list[dict[str, Any]],
    *,
    collection: str = "news_articles",
    pipeline: Any | None = None,
) -> dict[str, Any]:
    """Collapse duplicates, then ingest each canonical item once.

    ``pipeline`` is injectable so tests can assert delegation without a Qdrant.
    A single item's failure is counted, never fatal: one bad document must not
    drop the batch.
    """
    report: dict[str, Any] = {
        "received": len(items),
        "collapsed": 0,
        "ingested": 0,
        "duplicates": 0,
        "skipped": 0,
        "errors": 0,
        "ids": [],
    }
    if not items:
        return report
    canonical = collapse_items(items)
    report["collapsed"] = len(canonical)
    if pipeline is None:
        from rugbrain.firehose.pipeline import IngestionPipeline

        pipeline = IngestionPipeline()

    for item in canonical:
        target, content, metadata = _prepare(item, collection)
        try:
            result = await pipeline.ingest(target, content, metadata)
        except Exception as exc:  # one item's failure must not drop the batch
            report["errors"] += 1
            report.setdefault("failures", []).append(
                {"source": metadata.get("source"), "error": type(exc).__name__}
            )
            continue
        _record(report, result)
    return report
