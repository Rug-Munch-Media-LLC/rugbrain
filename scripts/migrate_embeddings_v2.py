"""Re-embed legacy 1024-dim Qdrant collections into rmi-qdrant-v2 at 2560-dim.

Reads points (payload only) from legacy rmi-qdrant (:6333), re-embeds the
stored text with Qwen3-Embedding-4B (llama.cpp :8004), recomputes BM25
sparse vectors where the target collection supports them, and upserts into
rmi-qdrant-v2 (:6334) keeping the same point UUIDs and payloads.

Checkpointed per collection — safe to interrupt; re-running resumes where
it left off. Hot collections first.

Usage:
    python3 scripts/migrate_embeddings_v2.py                # everything
    python3 scripts/migrate_embeddings_v2.py --collections news_v2,scam_intel_v2
    python3 scripts/migrate_embeddings_v2.py --batch 64 --limit 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("reembed")

LEGACY = os.getenv("LEGACY_QDRANT", "http://127.0.0.1:6333")
V2 = os.getenv("V2_QDRANT", "http://127.0.0.1:6334")
EMBEDDER = os.getenv("EMBEDDER_URL", "http://172.22.0.1:8004")
# API-embedder support: when EMBEDDER_MODEL is set the payload carries
# model/dimensions (DashScope compatible-mode); EMBEDDER_API_KEY adds the
# Authorization header. Unset → local llama.cpp (ignores both fields).
EMBEDDER_MODEL = os.getenv("EMBEDDER_MODEL", "")
EMBEDDER_API_KEY = os.getenv("EMBEDDER_API_KEY", "")
EMBEDDER_DIMS = int(os.getenv("EMBEDDER_DIMS", "2560"))
STATE_FILE = Path(os.getenv("STATE_FILE", "/var/lib/rmi-rag/reembed_state.json"))

# Field priority mirroring what ingestion embeds (content first).
TEXT_FIELDS = ("content", "text", "chunk_text", "title", "summary", "description", "name")

# Large collections first so progress is visible early.
HOT_FIRST = (
    "codebase_reference",
    "defi_exploits",
    "threat_intel_v2",
    "wallet_v2",
    "scam_patterns",
    "known_scams",
    "rgm_wallets",
    "news_v2",
    "scam_intel_v2",
)

INSTRUCT = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: {}"


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            loaded: dict[str, Any] = json.loads(STATE_FILE.read_text())
            return loaded
        except Exception:
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_FILE)


def _extract_text(payload: dict[str, Any]) -> str:
    for f in TEXT_FIELDS:
        v = payload.get(f) or (payload.get("metadata") or {}).get(f)
        if isinstance(v, str) and v.strip():
            return v[:4000]
    return ""


async def _supports_sparse(client: httpx.AsyncClient, collection: str) -> bool:
    r = await client.get(f"{V2}/collections/{collection}")
    if r.status_code != 200:
        return False
    vectors = r.json()["result"]["config"]["params"].get("vectors", {})
    return isinstance(vectors, dict) and "bm25" in vectors


def _embed_payload(texts: list[str]) -> dict[str, Any]:
    """Build /v1/embeddings body. API embedders need model+dimensions;
    local llama.cpp ignores unknown fields."""
    payload: dict[str, Any] = {"input": texts}
    if EMBEDDER_MODEL:
        payload["model"] = EMBEDDER_MODEL
        payload["dimensions"] = EMBEDDER_DIMS
    return payload


class _EmbedHTTPError(Exception):
    """Embedder HTTP failure with status for retry policy."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


async def _embed_batch(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """Embed a batch of documents (no instruction prefix — docs are raw)."""
    resp = await client.post(f"{EMBEDDER}/v1/embeddings", json=_embed_payload(texts))
    if resp.status_code != 200:
        raise _EmbedHTTPError(resp.status_code, resp.text)
    data = resp.json()["data"]
    data.sort(key=lambda x: x["index"])
    return [d["embedding"] for d in data]


async def _embed_batch_resilient(
    client: httpx.AsyncClient, texts: list[str]
) -> tuple[list[list[float]], list[int]]:
    """Embed a batch, surviving context-overflow poison items (2026-08-26).

    On HTTP 400 exceed_context_size: bisect the batch; a single offending
    item gets its text hard-truncated (3500 chars ≈ <900 tokens) and
    retried once. Returns (vectors, indices_dropped). Never raises for
    overflow — this bug crash-looped the migration for hours.
    """
    try:
        vecs = await _embed_batch(client, texts)
        return vecs, []
    except _EmbedHTTPError as e:
        if "exceed_context" not in str(e) or len(texts) == 1:
            # single poison item -> truncate hard and retry once
            t = texts[0][:3500]
            try:
                return await _embed_batch(client, [t]), []
            except _EmbedHTTPError:
                logger.warning("poison item dropped (unembeddable even truncated)")
                return [], [0]
        mid = len(texts) // 2
        left_v, left_d = await _embed_batch_resilient(client, texts[:mid])
        right_v, right_d = await _embed_batch_resilient(client, texts[mid:])
        dropped = left_d + [mid + d for d in right_d]
        return left_v + right_v, dropped


async def _embed_query(client: httpx.AsyncClient, query: str) -> list[float]:
    resp = await client.post(
        f"{EMBEDDER}/v1/embeddings", json=_embed_payload([INSTRUCT.format(query)])
    )
    if resp.status_code != 200:
        raise _EmbedHTTPError(resp.status_code, resp.text)
    embedding: list[float] = resp.json()["data"][0]["embedding"]
    return embedding


async def _sparse_for(text: str) -> dict[str, Any] | None:
    """Reuse rugbrain's fastembed BM25 sparse (mirrors qdrant_store.insert)."""
    try:
        from rugbrain.qdrant_store import _embed_sparse

        sparse = await _embed_sparse(text)
        if sparse is not None:
            return {"indices": sparse[0], "values": sparse[1]}
    except Exception as e:
        logger.warning("sparse_embed_failed: %s", e)
    return None


async def migrate_collection(
    client: httpx.AsyncClient,
    name: str,
    state: dict[str, Any],
    batch: int,
    limit: int,
    skip_sparse: bool = False,
    concurrent: int = 1,
) -> dict[str, Any]:
    """Re-embed one collection. Sequential per collection; collections run
    concurrently via asyncio.gather in main() (matches llama.cpp --parallel)."""
    info = (await client.get(f"{LEGACY}/collections/{name}")).json()["result"]
    total = info.get("points_count", 0)
    done = state.get(name, {}).get("done", 0)
    if total == 0 or done >= total:
        return {"name": name, "total": total, "done": total, "skipped": True}

    supports_sparse = await _supports_sparse(client, name)
    stats = {"name": name, "total": total, "done": done, "upserted": 0, "failed": 0, "no_text": 0}

    t0 = time.time()
    last_log = t0
    cursor: str | None = state.get(name, {}).get("cursor")
    offset = done
    while offset < total:
        r = await client.post(
            f"{LEGACY}/collections/{name}/points/scroll",
            json={"limit": batch, "offset": cursor, "with_payload": True, "with_vector": False},
        )
        r.raise_for_status()
        result = r.json()["result"]
        points = result.get("points", [])
        if not points:
            break
        cursor = result.get("next_page_offset")

        texts, payloads, ids = [], [], []
        for p in points:
            payload = p.get("payload") or {}
            text = _extract_text(payload)
            if not text:
                stats["no_text"] += 1
                continue
            texts.append(text)
            payloads.append(payload)
            ids.append(p["id"])

        if not texts:
            offset += len(points)
            continue

        # Embed the batch with ADAPTIVE SPLITTING (added 2026-08-24):
        # oversized docs can exceed the embedder's per-request window and
        # time out identically forever. On a timeout-class failure we HALVE
        # the working slice and retry immediately; a single doc that still
        # fails is skipped (logged) so one monster document can never wedge
        # the whole migration. Non-timeout errors keep the original
        # backoff/retry semantics.
        work_t, work_i, work_p = texts, ids, payloads
        _pending_slices: list[tuple[list, list, list]] = []
        consecutive_failures = 0
        _ctx_truncated = False  # local flag — never stored in payloads
        while True:
            try:
                vecs, dropped_idx = await _embed_batch_resilient(client, work_t)
                if dropped_idx:
                    logger.warning(
                        "%s: dropped %d unembeddable docs at offset=%d", name, len(dropped_idx), offset
                    )
                    keep = [i for i in range(len(work_t)) if i not in set(dropped_idx)]
                    work_t = [work_t[i] for i in keep]
                    work_i = [work_i[i] for i in keep]
                    work_p = [work_p[i] for i in keep]
                points_to_upsert = []
                for i, vec in enumerate(vecs):
                    point = {"id": work_i[i], "payload": work_p[i]}
                    vector: dict[str, Any] = {"dense": vec}
                    if supports_sparse and not skip_sparse:
                        sp = await _sparse_for(work_t[i])
                        if sp is not None:
                            vector["bm25"] = sp
                    point["vector"] = vector
                    points_to_upsert.append(point)
                # Batch upsert with retry
                for attempt in range(3):
                    try:
                        put = await client.put(
                            f"{V2}/collections/{name}/points",
                            json={"points": points_to_upsert},
                            timeout=300.0,
                        )
                        put.raise_for_status()
                        break
                    except Exception as e:
                        if attempt < 2:
                            logger.warning(
                                "upsert_retry %d/3 %s offset=%d: %s", attempt + 1, name, offset, e
                            )
                            await asyncio.sleep(5 * (attempt + 1))
                        else:
                            raise
                stats["upserted"] += len(points_to_upsert)
                offset += len(work_t)
                consecutive_failures = 0
                if _pending_slices:
                    work_t, work_i, work_p = _pending_slices.pop()
                    continue  # process remaining slice(s) of this batch
                break  # success — move to next scroll
            except _EmbedHTTPError as e:
                # Fail fast on permanent errors (bad model/dims/auth);
                # back off harder on rate limits.
                if 400 <= e.status < 500 and e.status != 429:
                    # 2026-08-26: exceed_context_size is NOT fatal — it means
                    # this slice's token count overflowed the embedder window.
                    # Halve multi-doc slices (same as timeouts); a single doc
                    # that alone overflows gets truncated to the char budget
                    # and retried once, then skipped. Crash-restarting on the
                    # same offset forever starves the shared embedder.
                    msg = str(e)
                    if "exceed" in msg.lower() and "context" in msg.lower():
                        if len(work_t) > 1:
                            half = max(1, len(work_t) // 2)
                            logger.warning(
                                "batch_split_ctx %s offset=%d: %d docs over ctx — halving to %d",
                                name,
                                offset,
                                len(work_t),
                                half,
                            )
                            _pending_slices.append((work_t[half:], work_i[half:], work_p[half:]))
                            work_t, work_i, work_p = work_t[:half], work_i[:half], work_p[:half]
                            continue
                        if not _ctx_truncated:
                            logger.warning(
                                "doc_TRUNCATE %s offset=%d: single doc over ctx — retrying at 60%% chars",
                                name,
                                offset,
                            )
                            _ctx_truncated = True
                            work_t = [work_t[0][: int(len(work_t[0]) * 0.6)]]
                            work_i, work_p = [work_i[0]], [work_p[0]]
                            continue
                        logger.error(
                            "doc_SKIP %s offset=%d: still over ctx after truncate — skipping",
                            name,
                            offset,
                        )
                        offset += len(work_t)
                        consecutive_failures = 0
                        if _pending_slices:
                            work_t, work_i, work_p = _pending_slices.pop()
                            continue
                        break
                    logger.error("batch_FATAL %s offset=%d: %s", name, offset, e)
                    raise
                consecutive_failures += 1
                wait = 30 if e.status == 429 else 15
                logger.error(
                    "batch_retry %s offset=%d (%d consec): %s — retrying in %ds",
                    name,
                    offset,
                    consecutive_failures,
                    e,
                    wait,
                )
                if consecutive_failures >= 20:
                    logger.error("batch_ABORT %s offset=%d: 20 consecutive failures", name, offset)
                    raise
                await asyncio.sleep(wait)
            except Exception as e:
                # Timeout-class failure on a multi-doc slice → HALVE it.
                if (
                    isinstance(e, (TimeoutError, asyncio.TimeoutError))
                    or "timeout" in str(e).lower()
                    or "ReadTimeout" in type(e).__name__
                ):
                    if len(work_t) > 1:
                        mid = max(1, len(work_t) // 2)
                        logger.warning(
                            "batch_split %s offset=%d: %d docs -> %d+%d (%r)",
                            name,
                            offset,
                            len(work_t),
                            mid,
                            len(work_t) - mid,
                            e,
                        )
                        right_t, right_i, right_p = work_t[mid:], work_i[mid:], work_p[mid:]
                        work_t, work_i, work_p = work_t[:mid], work_i[:mid], work_p[:mid]
                        # stash the remainder; after current slice succeeds we
                        # must process it before advancing offset.
                        _pending_slices.extend([(right_t, right_i, right_p)])
                        continue
                    logger.error(
                        "doc_SKIP %s offset=%d: single doc exceeds embedder budget (%r) — skipping",
                        name,
                        offset,
                        e,
                    )
                    stats["skipped_oversized"] = stats.get("skipped_oversized", 0) + 1
                    offset += 1
                    consecutive_failures = 0
                    break
                consecutive_failures += 1
                logger.error(
                    "batch_retry %s offset=%d (%d consec): %r — retrying in 15s",
                    name,
                    offset,
                    consecutive_failures,
                    e,
                )
                if consecutive_failures >= 20:
                    logger.error("batch_ABORT %s offset=%d: 20 consecutive failures", name, offset)
                    raise
                await asyncio.sleep(15)

        state.setdefault(name, {})["done"] = offset
        state[name]["cursor"] = cursor
        now = time.time()
        if now - last_log >= 60:
            last_log = now
            _save_state(state)
            rate = offset / max(now - t0, 1)
            eta = (total - offset) / max(rate, 0.01)
            logger.info(
                "%s %d/%d (%.0f%%) upserted=%d no_text=%d failed=%d rate=%.1f/s eta=%.0fm",
                name,
                offset,
                total,
                100 * offset / total,
                stats["upserted"],
                stats["no_text"],
                stats["failed"],
                rate,
                eta / 60,
            )
        if limit and offset >= limit:
            logger.info("%s: hit --limit %d, stopping", name, limit)
            break

    logger.info("collection done: %s %d/%d in %.0fs", name, offset, total, time.time() - t0)
    return stats


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collections", default="", help="comma-separated subset")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument(
        "--concurrent",
        type=int,
        default=3,
        help="concurrent collections (scroll/upsert overlap while embed is sequential)",
    )
    ap.add_argument("--limit", type=int, default=0, help="max points per collection")
    ap.add_argument(
        "--skip-sparse", action="store_true", help="skip BM25 sparse vector computation (faster)"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # max_keepalive_connections=0 → fresh TCP connection per request.
    # Qdrant (actix) closes idle keep-alive sockets (~75s) while our batches
    # take 50+s per embed — reusing a half-closed socket causes CLOSE-WAIT
    # hangs that freeze the whole migration. Fresh conns cost ~1ms each here.
    import httpx as _httpx

    headers = {}
    if EMBEDDER_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDER_API_KEY}"

    async with _httpx.AsyncClient(
        timeout=900.0,
        headers=headers,
        # keepalive>0: with 0, concurrent sends raced client connection
        # teardown -> 'Cannot send a request, as the client has been
        # closed' crash-restart loops (~400 items lost per cycle).
        limits=_httpx.Limits(max_keepalive_connections=20, max_connections=50),
    ) as client:
        cols = [
            c["name"]
            for c in (await client.get(f"{LEGACY}/collections")).json()["result"]["collections"]
        ]
        if args.collections:
            subset = {c.strip() for c in args.collections.split(",") if c.strip()}
            cols = [c for c in cols if c in subset]
        cols.sort(key=lambda c: (0 if c in HOT_FIRST else 1, c))

        state = _load_state()
        if args.dry_run:
            for c in cols:
                total = (
                    (await client.get(f"{LEGACY}/collections/{c}"))
                    .json()["result"]
                    .get("points_count", 0)
                )
                done = state.get(c, {}).get("done", 0)
                print(f"[dry-run] {c}: total={total} done={done} remaining={max(total - done, 0)}")
            return 0

        # sanity: embedder reachable
        try:
            probe = await _embed_batch(client, ["sanity probe"])
            if len(probe[0]) != EMBEDDER_DIMS:
                logger.error("embedder dim != %d (got %d) — aborting", EMBEDDER_DIMS, len(probe[0]))
                return 2
        except Exception as e:
            logger.error("embedder unreachable: %s", e)
            return 2

        # Run collections concurrently (semaphore caps in-flight embed
        # requests at llama.cpp --parallel slots). State file is written
        # per-collection, so parallel collections never clobber each other.
        sem = asyncio.Semaphore(max(1, args.concurrent))

        async def _run(c: str) -> dict[str, Any]:
            async with sem:
                return await migrate_collection(
                    client, c, state, args.batch, args.limit, args.skip_sparse, args.concurrent
                )

        summary = await asyncio.gather(*(_run(c) for c in cols))
        logger.info("MIGRATION COMPLETE: %s", json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
