"""Public bulk-embedding API for the corpus — now a chain, and fail-loud.

``embed_batch`` walks the lane chain from ``embed_chain`` (local :8024 8B/4096
first, then the hosted 8B lanes). Every attempt is dimension-checked against
``embed_contract``: a 1024-d lane can never be used, and a lane that returns
the wrong width is treated as failed rather than silently stored.

When EVERY lane fails the call raises ``EmbedLaneExhausted`` (the default;
``RUG_EMBED_STRICT=0`` restores the old ``[None, ...]`` return for diagnostics
only). History: returning ``[None, ...]`` on HTTP failure was read by every
caller as success, so a 402 sweep reported ``upserted=0`` and exited 0 while
the corpus stood still (2026-10-23, 493 embed_http_fail).
"""

from __future__ import annotations

import logging
import os

import httpx
from embed_chain import (
    Lane,
    build_chain,
    cooling,
    failure_count,
    freeze,
    last_failure,
    record_failure,
    reset_failures,
)
from embed_contract import CORPUS_DIM, MAX_CHARS, EmbedLaneExhausted, validate_dimensions

logger = logging.getLogger(__name__)

# Compat spellings: older callers and docs import these from here.
EMBED_DIM = CORPUS_DIM
QWEN_EMBED_DIM = CORPUS_DIM
QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8024/v1").rstrip("/")

# Statuses that will not fix themselves inside one sweep: park the lane.
_PERMANENT = frozenset({401, 402, 403})
__all__ = [
    "EMBED_DIM",
    "EmbedLaneExhausted",
    "embed_batch",
    "failure_count",
    "last_failure",
    "reset_failures",
    "resolve_lane",
]


def chain() -> list[Lane]:
    """The lane chain for this process, honouring ``EMBED_PROVIDER`` as a hint."""
    return build_chain(os.getenv("EMBED_PROVIDER", "").strip().lower() or None)


def resolve_lane() -> tuple[str, str, dict[str, str], str]:
    """``(provider, base_url, headers, model)`` for the FIRST usable lane.

    Kept for callers that only need "where would an embed go"; the real work
    goes through :func:`embed_batch`, which walks the whole chain.
    """
    lane = chain()[0]
    return (lane.name, lane.url, dict(lane.headers), lane.model)


async def _post(lane: Lane, texts: list[str], http: httpx.AsyncClient) -> list[list[float]] | None:
    """POST one batch to one lane. Records the failure itself, returns None."""
    payload = {"model": lane.model, "input": [t[:MAX_CHARS] for t in texts]}
    try:
        r = await http.post(lane.url, json=payload, headers=lane.headers, timeout=lane.timeout)
    except Exception as exc:
        record_failure(lane.name, "transport", f"{type(exc).__name__}: {exc}")
        return None
    if r.status_code != 200:
        record_failure(lane.name, f"http_{r.status_code}", r.text[:200])
        if r.status_code in _PERMANENT:
            freeze(lane)
        return None
    try:
        items = sorted(r.json().get("data", []), key=lambda i: i.get("index", 0))
    except ValueError as exc:
        record_failure(lane.name, "bad_json", str(exc))
        return None
    if len(items) != len(texts):
        record_failure(lane.name, "shape", f"got={len(items)} want={len(texts)}")
        return None
    vectors = validate_dimensions(items, CORPUS_DIM)
    if vectors is None:
        record_failure(lane.name, "dim", f"want={CORPUS_DIM}")
        return None
    return vectors


async def embed_batch(
    texts: list[str],
    *,
    client: httpx.AsyncClient | None = None,
    raise_on_failure: bool | None = None,
) -> list[list[float] | None]:
    """Embed *texts* on the first healthy 4096-d lane.

    Raises ``EmbedLaneExhausted`` when no lane produced vectors (default).
    Set ``RUG_EMBED_STRICT=0`` / ``raise_on_failure=False`` only for dry runs.
    """
    if not texts:
        return []
    lanes = build_chain(os.getenv("EMBED_PROVIDER", "").strip().lower() or None)
    own_client = client is None
    http = client or httpx.AsyncClient()
    try:
        tried: list[str] = []
        for lane in lanes:
            if cooling(lane):
                logger.info("embed_lane_parked lane=%s status=permanent", lane.name)
                continue
            tried.append(lane.name)
            vectors = await _post(lane, texts, http)
            if vectors is not None:
                if lane.name != lanes[0].name:
                    logger.warning("embed_lane_fallback used=%s preferred=%s", lane.name, lanes[0].name)
                return vectors
    finally:
        if own_client:
            await http.aclose()
    strict = raise_on_failure
    if strict is None:
        strict = os.getenv("RUG_EMBED_STRICT", "1") != "0"
    record_failure("chain", "exhausted", f"tried={','.join(tried) or 'none'} last={last_failure()}")
    if strict:
        raise EmbedLaneExhausted(
            f"no corpus lane produced vectors (tried={tried or 'none'}, want_dim={CORPUS_DIM}); "
            f"last={last_failure()}"
        )
    return [None] * len(texts)
