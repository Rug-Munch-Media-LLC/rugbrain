#!/usr/bin/env python3
"""News → Embed → Qdrant pipeline (RMI market-awareness RAG).

Reads `news_items` from Postgres (populated by rmi-backend RSS ingest),
chunks each article into 500-char sliding windows with 50-char overlap,
embeds every chunk with Qwen3-Embedding-8B (4096-dim) via the hosted lane,
and upserts the vectors into the Qdrant `news` collection with payload
metadata: title, source, url, published_at, category, sentiment.

Tracking / idempotency:
  - `news_items.rag_embedding_id` is set to the deterministic Qdrant
    point UUID of the first chunk (uuid5 over `news:{news_id}:0`) once
    every chunk has been upserted, so the marker always references a real
    Qdrant point. Rows where `rag_embedding_id IS NULL` or
    `rag_embedding_id = news_id` (the old fake marker) are re-embedded.
  - Chunk point ids are deterministic uuid5s, so re-runs upsert in place
    (no duplicate points).

Qdrant named-vector support:
  The `news` collection is an alias of `news_v2`, which is configured
  with named vectors (`{"dense": ...}`) plus BM25 sparse. A bare vector
  upsert against a named-vector collection is silently accepted by Qdrant
  (HTTP 200) but the point is NOT stored. This script probes the
  collection config and sends the named `{"dense": ...}` vector format so
  points actually persist (matching rugbrain.qdrant_store behavior).

Bounded runs: by default each run embeds at most 300 articles
(newest-first) so the 15-minute cron makes steady progress without
overlapping the next run. A lockfile prevents concurrent executions.

Run (host or inside a container with Postgres/Qdrant/reachable):

    python3 scripts/news_to_rag.py --limit 300
    python3 scripts/news_to_rag.py --purge-orphans   # delete points with no news_items row

Env (defaults target the docker-compose service names):
  POSTGRES_HOST/POSTGRES_PORT/POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB
  QDRANT_URL   (default http://127.0.0.1:6334, falls back to 127.0.0.1)
  EMBEDDER_URL (default http://127.0.0.1:8004/v1, falls back to 127.0.0.1)
  RAG_EMBED_MODEL (default qwen3-embedding-8b)
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import sys
import tempfile
import uuid
from typing import Any

import asyncpg
import httpx

from rugbrain.news_filter import is_noise_article

logger = logging.getLogger("news_to_rag")

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
EMBED_DIM = int(os.getenv("RAG_EMBED_DIM", "4096"))
QDRANT_COLLECTION = os.getenv("RAG_QDRANT_COLLECTION", "news_v2_8b")
UUID_NS = uuid.NAMESPACE_DNS  # same namespace as rugbrain.qdrant_store._to_uuid
LOCK_PATH = os.path.join(tempfile.gettempdir(), "news_to_rag.lock")
_ORDER_CLAUSES = {"asc": "ORDER BY published_at ASC", "desc": "ORDER BY published_at DESC"}
_named_cache: dict[str, bool] = {}


# ── URL resolution (docker names first, loopback fallback) ─────────────
def _resolve_base(name: str, primary: str, loopback: str) -> list[str]:
    candidates = [primary]
    # If the primary host is a docker service name, probe loopback too.
    # "0.0.0.0" here is a string comparison against candidate hostnames,
    # not a socket bind — B104 is a false positive.
    host = primary.split("//")[-1].split(":")[0]
    if host not in ("127.0.0.1", "localhost", "0.0.0.0"):  # noqa: S104  # nosec B104
        candidates.append(loopback)
    return candidates


def _iso_or_empty(value: object) -> str:
    """Return an ISO-8601 string for a datetime-like value, or '' when absent."""
    dt = getattr(value, "isoformat", None)
    return dt() if dt else ""


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding-window chunker: `size`-char windows with `overlap`-char overlap."""
    text = " ".join(text.split())  # normalize whitespace
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    chunks: list[str] = []
    for i in range(0, len(text), step):
        chunk = text[i : i + size].strip()
        if len(chunk) >= overlap:  # drop tiny trailing slivers
            chunks.append(chunk)
    return chunks


def _chunk_point_id(news_id: str, idx: int) -> str:
    return str(uuid.uuid5(UUID_NS, f"news:{news_id}:{idx}"))


def _derive_category(article: dict[str, Any]) -> str:
    chains = article.get("chains_mentioned") or []
    if chains:
        first: str = str(chains[0])
        return first
    return "crypto"


def _rate_article_payload(article: dict[str, Any], text: str) -> tuple[dict[str, Any], int]:
    """Run the multi-axis news rating engine over one article.

    Returns ``(rating_payload, quality_score)``. The payload carries the
    legacy ``ratings.py`` keys plus the seven 0-100 axes and
    ``quality_score``, so search-side boost/demote works unchanged.
    Fail-open: on any rating error return an empty payload + neutral 50 so
    ingestion never breaks on the enrichment step.
    """
    try:
        from rugbrain.news_rating import rate_article

        rating = rate_article(
            {
                "url": article.get("url") or "",
                "source": article.get("source") or "",
                "title": article.get("title") or "",
                "content": text,
                "published_at": article.get("published_at"),
                "corroborating_sources": 0,
                "chains_mentioned": article.get("chains_mentioned") or [],
                "tokens_mentioned": article.get("tokens_mentioned") or [],
            }
        )
        payload = rating.to_payload()
        return payload, int(payload.get("quality_score", 50))
    except Exception as e:
        logger.warning("news_rating_failed news_id=%s err=%s", article.get("news_id"), e)
        return {}, 50


# ── Qdrant REST helpers ────────────────────────────────────────────────
async def _ensure_collection(client: httpx.AsyncClient, base: str) -> bool:
    """Create the target collection (4096d Cosine) if missing. Idempotent."""
    r = await client.get(f"{base}/collections/{QDRANT_COLLECTION}")
    if r.status_code == 200:
        return True
    payload = {"vectors": {"size": EMBED_DIM, "distance": "Cosine"}}
    r = await client.put(f"{base}/collections/{QDRANT_COLLECTION}", json=payload)
    if r.status_code in (200, 201):
        logger.info("qdrant_collection_created collection=%s", QDRANT_COLLECTION)
        return True
    logger.warning("qdrant_collection_create_failed status=%d body=%s", r.status_code, r.text[:200])
    return False


async def _collection_uses_named_vectors(client: httpx.AsyncClient, base: str) -> bool:
    """Return True when the `news_v2` collection is configured with named vectors.

    Qdrant 1.18 silently drops (HTTP 200, no store) a bare-vector upsert
    against a named-vector collection such as ``news_v2``. Detect the
    ``dense`` key in the collection's vectors config
    so the caller sends the named vector format. Results are cached per
    run so we only probe once.
    """
    if _named_cache.get(QDRANT_COLLECTION) is not None:
        return _named_cache[QDRANT_COLLECTION]
    named = False
    try:
        r = await client.get(f"{base}/collections/{QDRANT_COLLECTION}")
        if r.status_code == 200:
            params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
            vectors = params.get("vectors")
            if isinstance(vectors, dict) and "dense" in vectors:
                named = True
            if params.get("sparse_vectors"):
                named = True
    except Exception as e:
        logger.debug("qdrant_named_probe_failed collection=%s err=%s", QDRANT_COLLECTION, e)
    _named_cache[QDRANT_COLLECTION] = named
    return named


async def _upsert_batch(client: httpx.AsyncClient, base: str, points: list[dict[str, Any]]) -> bool:
    if not points:
        return True
    named = await _collection_uses_named_vectors(client, base)
    payload_points = []
    for p in points:
        pp = dict(p)
        if named and isinstance(pp.get("vector"), list):
            # Send the named dense format — a bare vector is silently
            # dropped (HTTP 200 but point not stored) by Qdrant against
            # named-vector collections.
            pp["vector"] = {"dense": pp["vector"]}
        payload_points.append(pp)
    r = await client.put(
        f"{base}/collections/{QDRANT_COLLECTION}/points",
        json={"points": payload_points},
    )
    if r.status_code not in (200, 201):
        logger.warning("qdrant_upsert_failed status=%d body=%s", r.status_code, r.text[:300])
        return False
    return True


# ── Qwen3 embed helpers ─────────────────────────────────────────────
async def _embed_batch(
    client: httpx.AsyncClient, base: str, model: str, texts: list[str]
) -> list[list[float]] | None:
    """Embed a list of texts on the bulk lane. Returns None on failure.

    DeepInfra-first (same Qwen3-Embedding-8B as the local server, ~80x
    faster); the passed ``base``/``model`` apply on the local lane. See
    scripts/embed_lane.py for the provider policy.
    """
    if not texts:
        return []
    from embed_lane import resolve_lane

    _provider, lane_base, lane_headers, lane_model = resolve_lane()
    # Any hosted lane supplies its own base/model; only `local` uses the
    # EMBEDDER_URL/--model passed in by the caller.
    hosted = _provider != "local"
    base = lane_base if hosted else base
    model = lane_model if hosted else model
    # DEEPINFRA_EMBED_URL is written both as a base and as a full endpoint in
    # the fleet's env files; appending blindly produced
    # POST .../v1/embeddings/embeddings -> 404 and silently failed every hosted
    # embed (seen 2026-10-22: articles_embedded=0, errors=1665). Same guard as
    # embed_lane.embed_batch.
    base = base.rstrip("/")
    url = base if base.endswith("/embeddings") else f"{base}/embeddings"
    try:
        r = await client.post(
            url,
            headers=lane_headers,
            json={"model": model, "input": texts},
            timeout=httpx.Timeout(300.0),
        )
        if r.status_code != 200:
            logger.warning("_embed_failed status=%d body=%s", r.status_code, r.text[:200])
            return None
        payload = r.json()
        items = payload.get("data") if isinstance(payload, dict) else payload
        items = items or []
        embeddings = []
        for item in sorted(items, key=lambda i: i.get("index", 0)):
            emb = item.get("embedding", [])
            if emb and isinstance(emb[0], list):
                emb = emb[0]
            embeddings.append(emb)
        if not embeddings or len(embeddings) != len(texts):
            logger.warning("_embed_shape_mismatch got=%d want=%d", len(embeddings), len(texts))
            return None
        for vec in embeddings:
            if len(vec) != EMBED_DIM:
                logger.warning("_embed_dim got=%d want=%d", len(vec), EMBED_DIM)
                return None
        return embeddings
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("_embed_error err=%s", e)
        return None


# ── Main pipeline ──────────────────────────────────────────────────────
async def _resolve_and_connect() -> tuple[asyncpg.Connection, str, str]:
    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    pg_port = int(os.getenv("POSTGRES_PORT", "5432"))
    pg_user = os.getenv("POSTGRES_USER", "rmi")
    pg_pass = os.getenv("POSTGRES_PASSWORD", "")
    pg_db = os.getenv("POSTGRES_DB", "rmi")

    qdrant_candidates = _resolve_base(
        "qdrant",
        os.getenv("QDRANT_URL", "http://127.0.0.1:6334"),
        "http://127.0.0.1:6334",
    )
    _candidates = _resolve_base(
        "",
        os.getenv("EMBEDDER_URL", os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1")),
        "http://127.0.0.1:8004/v1",
    )

    # Postgres: try configured host, then loopback
    pg_hosts = [pg_host]
    if pg_host not in ("127.0.0.1", "localhost"):
        pg_hosts.append("127.0.0.1")

    conn = None
    last_err: Exception | None = None
    for host in pg_hosts:
        try:
            conn = await asyncpg.connect(
                host=host,
                port=pg_port,
                user=pg_user,
                password=pg_pass,
                database=pg_db,
                timeout=8,
            )
            break
        except (OSError, asyncpg.PostgresError) as e:
            last_err = e
            logger.debug("pg_connect_failed host=%s err=%s", host, e)
    if conn is None:
        raise RuntimeError(f"postgres_unreachable: {last_err}")

    return conn, qdrant_candidates[0], _candidates[0]


async def purge_orphans() -> dict[str, Any]:
    """Delete Qdrant ``news`` (alias of ``news_v2``) points with no news_items row.

    Two orphan classes are removed:
      1. Legacy "story aggregation" points that carry no ``metadata.news_id``
         at all (pre-pipeline schema).
      2. Points whose ``metadata.news_id`` no longer exists in Postgres
         ``news_items`` (rows pruned/never committed).

    Runs under the same lockfile as the ingest pipeline so a purge never
    races an embed run. Deleting via the ``news`` alias hits the same
    underlying ``news_v2`` collection the pipeline writes to.
    """
    lock = await asyncio.to_thread(open, LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.warning("lock_held_skipping (another news_to_rag run in progress)")
        return {"status": "skipped", "reason": "lock_held"}

    conn, qdrant_base, __base = await _resolve_and_connect()
    stats: dict[str, Any] = {
        "points_scanned": 0,
        "legacy_no_news_id": 0,
        "missing_from_db": 0,
        "points_deleted": 0,
        "errors": 0,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Scroll every point id + its metadata.news_id (payload only,
            #    no vectors — cheap).
            news_id_by_point: dict[str, str | None] = {}
            next_offset: object = None
            while True:
                scroll_payload: dict[str, Any] = {
                    "limit": 1000,
                    "with_payload": ["metadata"],
                    "with_vector": False,
                }
                if next_offset is not None:
                    scroll_payload["offset"] = next_offset
                r = await client.post(
                    f"{qdrant_base}/collections/{QDRANT_COLLECTION}/points/scroll",
                    json=scroll_payload,
                )
                if r.status_code != 200:
                    stats["errors"] += 1
                    logger.warning(
                        "qdrant_scroll_failed status=%d body=%s", r.status_code, r.text[:300]
                    )
                    break
                result = r.json().get("result", {})
                points = result.get("points", [])
                stats["points_scanned"] += len(points)
                for p in points:
                    metadata = (p.get("payload") or {}).get("metadata") or {}
                    news_id = metadata.get("news_id")
                    news_id_by_point[str(p["id"])] = news_id if isinstance(news_id, str) else None
                next_offset = result.get("next_page_offset")
                if not points or next_offset is None:
                    break

            if not news_id_by_point:
                logger.info("purge_orphans: no points to inspect")
                return stats

            # 2. Find which news_ids actually exist in Postgres (batched).
            news_ids = sorted({n for n in news_id_by_point.values() if n is not None})
            existing: set[str] = set()
            for i in range(0, len(news_ids), 500):
                chunk = news_ids[i : i + 500]
                rows = await conn.fetch(
                    "SELECT news_id FROM news_items WHERE news_id = ANY($1::text[])",
                    chunk,
                )
                existing.update(r["news_id"] for r in rows)

            # 3. Classify orphans.
            legacy_ids = [pid for pid, nid in news_id_by_point.items() if nid is None]
            missing_ids = [
                pid
                for pid, nid in news_id_by_point.items()
                if nid is not None and nid not in existing
            ]
            orphan_ids = legacy_ids + missing_ids
            stats["legacy_no_news_id"] = len(legacy_ids)
            stats["missing_from_db"] = len(missing_ids)
            logger.info(
                "purge_orphans scanned=%d legacy_no_news_id=%d missing_from_db=%d total_orphans=%d",
                len(news_id_by_point),
                len(legacy_ids),
                len(missing_ids),
                len(orphan_ids),
            )

            # 4. Delete in batches.
            for i in range(0, len(orphan_ids), 1000):
                batch = orphan_ids[i : i + 1000]
                r = await client.post(
                    f"{qdrant_base}/collections/{QDRANT_COLLECTION}/points/delete",
                    json={"points": batch},
                )
                if r.status_code in (200, 201):
                    stats["points_deleted"] += len(batch)
                    logger.info(
                        "purge_orphans_deleted batch=%d total_deleted=%d",
                        len(batch),
                        stats["points_deleted"],
                    )
                else:
                    stats["errors"] += 1
                    logger.warning(
                        "qdrant_delete_failed status=%d body=%s", r.status_code, r.text[:300]
                    )

        await conn.execute("COMMIT")
    finally:
        await conn.close()
        lock.close()

    logger.info(
        "purge_orphans complete: scanned=%d legacy_no_news_id=%d missing_from_db=%d deleted=%d errors=%d",
        stats["points_scanned"],
        stats["legacy_no_news_id"],
        stats["missing_from_db"],
        stats["points_deleted"],
        stats["errors"],
    )
    return stats


async def run(
    limit: int, max_chunks: int, embed_model: str, newest_first: bool = True
) -> dict[str, Any]:
    lock = await asyncio.to_thread(open, LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.warning("lock_held_skipping (another news_to_rag run in progress)")
        return {"status": "skipped", "reason": "lock_held"}

    conn, qdrant_base, _base = await _resolve_and_connect()
    logger.info("connected pg=ok qdrant=%s =%s", qdrant_base, _base)

    stats = {
        "articles_scanned": 0,
        "articles_embedded": 0,
        "chunks_embedded": 0,
        "points_upserted": 0,
        "errors": 0,
    }

    try:
        # Pick up rows that were never embedded OR carry the old fake marker
        # (rag_embedding_id == news_id was the pre-pipeline placeholder).
        # SQL is static: only _ORDER_CLAUSES ("asc"/"desc" fixed strings) is
        # interpolated; the limit is bound via the $1 parameter.
        query = (
            "SELECT news_id, url, title, summary, body_markdown, source, "  # nosec B608
            "published_at, chains_mentioned, tokens_mentioned, sentiment_score "
            "FROM news_items "
            "WHERE rag_embedding_id IS NULL OR rag_embedding_id = news_id "
            + _ORDER_CLAUSES["desc" if newest_first else "asc"]
            + " LIMIT $1"
        )
        rows = await conn.fetch(query, limit)
        stats["articles_scanned"] = len(rows)
        logger.info("candidate_articles=%d limit=%d", len(rows), limit)
        if not rows:
            return stats

        async with httpx.AsyncClient(timeout=60.0) as client:
            if not await _ensure_collection(client, qdrant_base):
                raise RuntimeError("qdrant_collection_unavailable")

            # Pre-chunk every candidate article up to the run budget so we can
            # embed batches spanning multiple articles (input arrays
            # amortize per-call overhead — 16 chunks/call is ~4x faster than
            # one tiny call per article).
            articles: list[tuple[dict[str, Any], list[str]]] = []
            total_chunks = 0
            for row in rows:
                article = dict(row)
                text = "\n".join(
                    filter(
                        None,
                        [
                            article.get("title") or "",
                            article.get("summary") or "",
                            article.get("body_markdown") or "",
                        ],
                    )
                )
                chunks = _chunk_text(text)
                if not chunks:
                    continue
                if total_chunks + len(chunks) > max_chunks:
                    articles.append((article, chunks[: max(0, max_chunks - total_chunks)]))
                    break
                articles.append((article, chunks))
                total_chunks += len(chunks)

            logger.info(
                "prechunked articles=%d chunks=%d budget=%d",
                len(articles),
                total_chunks,
                max_chunks,
            )

            # (article, chunk_idx, chunk_text, vector) flat list; vectors
            # filled by batch embed. chunk_idx is the PER-ARTICLE index —
            # using the global flat index here corrupts point IDs (chunk 0 of
            # article N would get uuid5 "news:{id}:{global_idx}", never
            # matching the first-chunk marker "news:{id}:0").
            work: list[list[Any]] = [
                [article, chunk_idx, chunk, None]
                for article, chunks in articles
                for chunk_idx, chunk in enumerate(chunks)
            ]

            batch_size = int(os.getenv("NEWS_TO_RAG_BATCH", "16"))
            for i in range(0, len(work), batch_size):
                batch = work[i : i + batch_size]
                texts = [item[2] for item in batch]
                vecs = await _embed_batch(client, _base, embed_model, texts)
                if vecs is None:
                    stats["errors"] += len(batch)
                    continue
                for item, vec in zip(batch, vecs, strict=True):
                    item[3] = vec

            # Group completed vectors by article and upsert
            for article, chunks in articles:
                news_id = article["news_id"]
                if is_noise_article(article):
                    stats["skipped_noise"] = stats.get("skipped_noise", 0) + 1
                    continue
                text = "\n".join(
                    filter(
                        None,
                        [
                            article.get("title") or "",
                            article.get("summary") or "",
                            article.get("body_markdown") or "",
                        ],
                    )
                )
                rating_payload, quality_score = _rate_article_payload(article, text)
                points = []
                chunk_total_for_article = 0
                for a, chunk_idx, chunk, vec in work:
                    if a["news_id"] != news_id or vec is None:
                        continue
                    points.append(
                        {
                            "id": _chunk_point_id(news_id, chunk_idx),
                            "vector": vec,
                            "payload": {
                                "content": chunk,
                                "metadata": {
                                    "title": article.get("title") or "",
                                    "source": article.get("source") or "",
                                    "url": article.get("url") or "",
                                    "published_at": (_iso_or_empty(article.get("published_at"))),
                                    "category": _derive_category(article),
                                    "sentiment": article.get("sentiment_score"),
                                    "chains_mentioned": article.get("chains_mentioned") or [],
                                    "tokens_mentioned": article.get("tokens_mentioned") or [],
                                    "news_id": news_id,
                                    # Ground News-style multi-axis rating
                                    # (see rugbrain.news_rating). quality_score
                                    # mirrors composite for Qdrant range filters.
                                    "rating": rating_payload,
                                    "quality_score": quality_score,
                                },
                                "source": article.get("source") or "",
                                "severity": "info",
                                "chain": (article.get("chains_mentioned") or [""])[0],
                                "collection": QDRANT_COLLECTION,
                            },
                        }
                    )
                    chunk_total_for_article += 1
                    if chunk_total_for_article >= len(chunks):
                        break

                if not points:
                    stats["errors"] += 1
                    continue

                if not await _upsert_batch(client, qdrant_base, points):
                    stats["errors"] += 1
                    continue

                # Mark article embedded with the first chunk's deterministic
                # Qdrant point id — the marker must reference a real point
                # (chunk ids are `uuid5("news:{news_id}:{idx}")`, NOT the
                # article-level id which is never a point).
                await conn.execute(
                    "UPDATE news_items SET rag_embedding_id=$1 WHERE news_id=$2",
                    _chunk_point_id(news_id, 0),
                    news_id,
                )
                stats["articles_embedded"] += 1
                stats["chunks_embedded"] += len(points)
                stats["points_upserted"] += len(points)
                logger.info("embedded news_id=%s chunks=%d", news_id, len(points))

        await conn.execute("COMMIT")
    finally:
        await conn.close()
        lock.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="News → embed → Qdrant pipeline")
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("NEWS_TO_RAG_LIMIT", "300")),
        help="max articles per run (default 300; 0 = unlimited)",
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=int(os.getenv("NEWS_TO_RAG_MAX_CHUNKS", "4096")),
        help="max chunks per run (default 4096; 0 = unlimited)",
    )
    parser.add_argument("--model", default=os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b"))
    parser.add_argument(
        "--oldest-first",
        action="store_true",
        help="embed oldest articles first (default is newest-first)",
    )
    parser.add_argument(
        "--purge-orphans",
        action="store_true",
        help=(
            "delete news_v2 points that have no corresponding news_items row "
            "(legacy no-news_id points + news_id missing from Postgres); "
            "skips the embed pipeline entirely"
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    limit = args.limit if args.limit > 0 else 100_000
    max_chunks = args.max_chunks if args.max_chunks > 0 else 10_000_000

    try:
        if args.purge_orphans:
            stats = asyncio.run(purge_orphans())
        else:
            stats = asyncio.run(
                run(limit, max_chunks, args.model, newest_first=not args.oldest_first)
            )
    except Exception as e:  # surface cleanly to cron/journald
        logger.error("news_to_rag_failed err=%s", e)
        sys.exit(1)

    print(json.dumps({"status": "ok", **stats}, indent=2, default=str))


if __name__ == "__main__":
    main()
