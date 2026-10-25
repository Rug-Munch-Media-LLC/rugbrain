"""Part 9/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F403
from _parts_ingest_scam_knowledge.p2 import (
    Checkpoint,
    _embed_batch,
    _ensure_collection,
    _upsert_batch,
)
from _parts_ingest_scam_knowledge.p3 import _build_point
from _parts_ingest_scam_knowledge.p8 import _collect_docs


async def _ingest_source(
    source: str,
    client: httpx.AsyncClient,
    checkpoint: Checkpoint,
    limit: int | None,
    dry_run: bool,
    force: bool,
    fetch_rekt: bool,
    dedup: bool = True,
    dedup_threshold: float = DEDUP_THRESHOLD_DEFAULT,
    priority_only: str | None = None,
    refresh: bool = False,
) -> tuple[int, int, int, int]:
    """Ingest one source.

    Returns (upserted, skipped_by_checkpoint, total_chunks, dedup_skipped).
    """
    collection = SOURCE_COLLECTIONS[source]
    docs = await _collect_docs(source, limit, fetch_rekt, refresh=refresh)
    if not docs:
        logger.info("source=%s no docs collected", source)
        return 0, 0, 0, 0
    if priority_only:
        docs = filter_by_severity(docs, priority_only)
        logger.info("source=%s priority_only=%s kept=%d", source, priority_only, len(docs))
    # Exploit/critical content first, reference/taxonomy later.
    docs = sort_by_priority(docs)
    await _ensure_collection(client, collection)
    logger.info("source=%s docs=%d collection=%s", source, len(docs), collection)

    upserted = 0
    skipped = 0
    total_chunks = 0
    dedup_skipped = 0
    pending: list[dict[str, Any]] = []  # (rel_key, text, metadata) not yet embedded

    for doc in docs:
        text = doc["text"]
        metadata = dict(doc["metadata"])
        metadata["title"] = doc.get("title", "")
        rel_key = doc["rel_key"]
        if not force and checkpoint.is_done(source, rel_key, text):
            skipped += 1
            continue
        pending.append({"rel_key": rel_key, "text": text, "metadata": metadata})
        total_chunks += 1

    if dry_run:
        logger.info(
            "[DRY RUN] source=%s would ingest %d chunks (skipped %d)",
            source,
            len(pending),
            skipped,
        )
        return 0, skipped, len(pending), 0

    # Embed in batches of 50, upsert in batches of 50.
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        texts = [b["text"] for b in batch]
        vectors = await _embed_batch(texts)
        # Cross-reference new chunks against existing Qdrant content; chunks
        # with a top hit >= dedup_threshold are already ingested → skip.
        dups = (
            await dedup_vectors(vectors, collection, client, dedup_threshold, QDRANT_URL)
            if dedup
            else [False] * len(vectors)
        )
        points: list[dict[str, Any]] = []
        for b, vec, is_dup in zip(batch, vectors, dups, strict=False):
            if is_dup:
                dedup_skipped += 1
                checkpoint.mark(source, b["rel_key"], b["text"])  # don't re-embed next run
                continue
            if vec is None:
                logger.warning("embed_missing rel_key=%s", b["rel_key"][:60])
                continue
            points.append(
                _build_point(
                    source=source,
                    collection=collection,
                    rel_key=b["rel_key"],
                    content=b["text"],
                    metadata=b["metadata"],
                    vector=vec,
                )
            )
            checkpoint.mark(source, b["rel_key"], b["text"])
        if not points:
            checkpoint.save()
            continue
        ok = await _upsert_batch(client, collection, points)
        upserted += ok
        checkpoint.save()
        if (i // BATCH_SIZE + 1) % 5 == 0 or i + BATCH_SIZE >= len(pending):
            logger.info(
                "source=%s progress %d/%d chunks upserted=%d dedup_skipped=%d",
                source,
                min(i + BATCH_SIZE, len(pending)),
                len(pending),
                upserted,
                dedup_skipped,
            )

    # Freshness: record when this source was last updated (git commit time
    # for cloned repos, now for API sources).
    if source in SOURCE_TABLE:
        stamp = repo_last_updated(str(KNOWLEDGE_DIR / SOURCE_TABLE[source][1])) or ""
    else:
        stamp = datetime.now(UTC).isoformat()
    if stamp:
        checkpoint.set_last_updated(source, stamp)
        checkpoint.save()

    logger.info(
        "source=%s done upserted=%d skipped=%d total_chunks=%d dedup_skipped=%d last_updated=%s",
        source,
        upserted,
        skipped,
        total_chunks,
        dedup_skipped,
        stamp or "unknown",
    )
    return upserted, skipped, total_chunks, dedup_skipped


__all__ = [
    "_ingest_source",
]
