#!/usr/bin/env python3
"""News → Embed → Qdrant pipeline (RMI market-awareness RAG).

Reads `news_items` from Postgres (populated by rmi-backend RSS ingest),
chunks each article into 500-char sliding windows with 50-char overlap,
embeds every chunk with BGE-M3 (1024-dim) via Infinity `/embeddings`,
and upserts the vectors into the Qdrant `news` collection with payload
metadata: title, source, url, published_at, category, sentiment.

Tracking / idempotency:
  - `news_items.rag_embedding_id` is set to the deterministic Qdrant
    point UUID for the article (uuid5 over `news:{news_id}`) once every
    chunk has been upserted. Rows where `rag_embedding_id IS NULL` or
    `rag_embedding_id = news_id` (the old fake marker) are re-embedded.
  - Chunk point ids are deterministic uuid5s, so re-runs upsert in place
    (no duplicate points).

Bounded runs: by default each run embeds at most 300 articles
(newest-first) so the 15-minute cron makes steady progress without
overlapping the next run. A lockfile prevents concurrent executions.

Run (host or inside a container with Postgres/Qdrant/Infinity reachable):

    python3 scripts/news_to_rag.py --limit 300

Env (defaults target the docker-compose service names):
  POSTGRES_HOST/POSTGRES_PORT/POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB
  QDRANT_URL   (default http://localhost:6333, falls back to 127.0.0.1)
  INFINITY_URL (default http://localhost:8001, falls back to 127.0.0.1)
  RAG_INFINITY_EMBED_MODEL (default BAAI/bge-m3)
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

import asyncpg
import httpx

logger = logging.getLogger("news_to_rag")

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
EMBED_DIM = 1024
QDRANT_COLLECTION = "news"
UUID_NS = uuid.NAMESPACE_DNS  # same namespace as rmi_rag.qdrant_store._to_uuid
LOCK_PATH = os.path.join(tempfile.gettempdir(), "news_to_rag.lock")
_ORDER_CLAUSES = {"asc": "ORDER BY published_at ASC", "desc": "ORDER BY published_at DESC"}


# ── URL resolution (docker names first, loopback fallback) ─────────────
def _resolve_base(name: str, primary: str, loopback: str) -> list[str]:
    candidates = [primary]
    # If the primary host is a docker service name, probe loopback too
    host = primary.split("//")[-1].split(":")[0]
    if host not in ("127.0.0.1", "localhost", "0.0.0.0"):  # noqa: S104
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


def _article_point_id(news_id: str) -> str:
    return str(uuid.uuid5(UUID_NS, f"news:{news_id}"))


def _chunk_point_id(news_id: str, idx: int) -> str:
    return str(uuid.uuid5(UUID_NS, f"news:{news_id}:{idx}"))


def _derive_category(article: dict) -> str:
    chains = article.get("chains_mentioned") or []
    if chains:
        return chains[0]
    return "crypto"


def _rate_article_payload(article: dict, text: str) -> tuple[dict, int]:
    """Run the multi-axis news rating engine over one article.

    Returns ``(rating_payload, quality_score)``. The payload carries the
    legacy ``ratings.py`` keys plus the seven 0-100 axes and
    ``quality_score``, so search-side boost/demote works unchanged.
    Fail-open: on any rating error return an empty payload + neutral 50 so
    ingestion never breaks on the enrichment step.
    """
    try:
        from rmi_rag.news_rating import rate_article

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
    """Create the `news` collection (1024d Cosine) if missing. Idempotent."""
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


async def _upsert_batch(client: httpx.AsyncClient, base: str, points: list[dict]) -> bool:
    if not points:
        return True
    r = await client.put(
        f"{base}/collections/{QDRANT_COLLECTION}/points",
        json={"points": points},
    )
    if r.status_code not in (200, 201):
        logger.warning("qdrant_upsert_failed status=%d body=%s", r.status_code, r.text[:300])
        return False
    return True


# ── Infinity embed helpers ─────────────────────────────────────────────
async def _embed_batch(
    client: httpx.AsyncClient, base: str, model: str, texts: list[str]
) -> list[list[float]] | None:
    """Embed a list of texts via Infinity `/embeddings`. Returns None on failure."""
    if not texts:
        return []
    try:
        r = await client.post(
            f"{base}/embeddings",
            json={"model": model, "input": texts},
            timeout=httpx.Timeout(300.0),
        )
        if r.status_code != 200:
            logger.warning("infinity_embed_failed status=%d body=%s", r.status_code, r.text[:200])
            return None
        items = r.json().get("data") or []
        embeddings = [
            item.get("embedding", []) for item in sorted(items, key=lambda i: i.get("index", 0))
        ]
        if not embeddings or len(embeddings) != len(texts):
            logger.warning(
                "infinity_embed_shape_mismatch got=%d want=%d", len(embeddings), len(texts)
            )
            return None
        for vec in embeddings:
            if len(vec) != EMBED_DIM:
                logger.warning("infinity_embed_dim got=%d want=%d", len(vec), EMBED_DIM)
                return None
        return embeddings
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("infinity_embed_error err=%s", e)
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
        os.getenv("QDRANT_URL", "http://localhost:6333"),
        "http://127.0.0.1:6333",
    )
    infinity_candidates = _resolve_base(
        "infinity",
        os.getenv("INFINITY_URL", "http://localhost:8001"),
        "http://127.0.0.1:8001",
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

    return conn, qdrant_candidates[0], infinity_candidates[0]


async def run(limit: int, max_chunks: int, embed_model: str, newest_first: bool = True) -> dict:
    lock = await asyncio.to_thread(open, LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.warning("lock_held_skipping (another news_to_rag run in progress)")
        return {"status": "skipped", "reason": "lock_held"}

    conn, qdrant_base, infinity_base = await _resolve_and_connect()
    logger.info("connected pg=ok qdrant=%s infinity=%s", qdrant_base, infinity_base)

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
        query = (
            "SELECT news_id, url, title, summary, body_markdown, source, "
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
            # embed batches spanning multiple articles (Infinity input arrays
            # amortize per-call overhead — 16 chunks/call is ~4x faster than
            # one tiny call per article).
            articles: list[tuple[dict, list[str]]] = []
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

            # (article, chunk_text, vector) flat list; vectors filled by batch embed
            work: list[list] = [
                [article, chunk, None] for article, chunks in articles for chunk in chunks
            ]

            batch_size = 16
            for i in range(0, len(work), batch_size):
                batch = work[i : i + batch_size]
                texts = [item[1] for item in batch]
                vecs = await _embed_batch(client, infinity_base, embed_model, texts)
                if vecs is None:
                    stats["errors"] += len(batch)
                    continue
                for item, vec in zip(batch, vecs, strict=True):
                    item[2] = vec

            # Group completed vectors by article and upsert
            for article, chunks in articles:
                news_id = article["news_id"]
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
                for idx, (a, chunk, vec) in enumerate(work):
                    if a["news_id"] != news_id or vec is None:
                        continue
                    points.append(
                        {
                            "id": _chunk_point_id(news_id, idx),
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
                                    # (see rmi_rag.news_rating). quality_score
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

                # Mark article embedded with its deterministic article point id
                await conn.execute(
                    "UPDATE news_items SET rag_embedding_id=$1 WHERE news_id=$2",
                    _article_point_id(news_id),
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
    parser.add_argument("--model", default=os.getenv("RAG_INFINITY_EMBED_MODEL", "BAAI/bge-m3"))
    parser.add_argument(
        "--oldest-first",
        action="store_true",
        help="embed oldest articles first (default is newest-first)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    limit = args.limit if args.limit > 0 else 100_000
    max_chunks = args.max_chunks if args.max_chunks > 0 else 10_000_000

    try:
        stats = asyncio.run(run(limit, max_chunks, args.model, newest_first=not args.oldest_first))
    except Exception as e:  # surface cleanly to cron/journald
        logger.error("news_to_rag_failed err=%s", e)
        sys.exit(1)

    print(json.dumps({"status": "ok", **stats}, indent=2, default=str))


if __name__ == "__main__":
    main()
