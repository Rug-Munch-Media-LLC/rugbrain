#!/usr/bin/env python3
"""Shared provider-aware batch embedding for RugBrain ingest lanes.

One lane decision for every bulk ingester:

  * ``deepinfra`` (default when a key is configured) — hosted
    ``Qwen/Qwen3-Embedding-4B`` — the SAME 2560-dim vector space as the
    local llama.cpp lane, ~80x faster (~23 ms/doc vs ~1947 ms/doc local,
    benchmarked 2026-08-29).  Bulk ingestion is heavy by nature and must
    NOT saturate the CPU-capped local server (2026-09-14 runaway:
    local-only ingests ground for days, starved interactive queries, and
    filled the logs with embed_missing retries).
  * ``local`` — llama.cpp ``/embeddings`` (``QWEN_EMBED_URL``, :8004).
    Used when no DeepInfra key is configured, or explicitly via
    ``EMBED_PROVIDER=local``.  Intended for light server-dependent work,
    never for bulk.

Selection (``EMBED_PROVIDER`` env): ``deepinfra`` | ``local`` | unset
(``auto``: DeepInfra when a key is available, else local).

Dim policy: canonical corpus dim is 2560 (qwen3-embedding-4b native,
identical locally and on DeepInfra).  Override with ``QWEN_EMBED_DIMS``
or ``RAG_EMBED_DIM`` only if a collection is deliberately MRL-truncated.

The API key is read from ``DEEPINFRA_API_KEY`` or, when unset, the file
at ``DEEPINFRA_KEY_FILE`` (default ``/run/secrets/deepinfra_api_key``;
the fleet exports ``/srv/secrets/deepinfra_api_key``).  Never logged.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1").rstrip("/")
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
DEEPINFRA_EMBED_URL = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1").rstrip("/")
DEEPINFRA_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
DEEPINFRA_KEY_FILE = os.getenv("DEEPINFRA_KEY_FILE", "/run/secrets/deepinfra_api_key")
# Canonical dim — qwen3-embedding-4b native, same local and hosted.
EMBED_DIM = int(os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or "2560")

_TIMEOUT = {
    "deepinfra": float(os.getenv("DEEPINFRA_EMBED_TIMEOUT", "120")),
    "local": float(os.getenv("RAG_EMBED_TIMEOUT", "300")),
}


def _deepinfra_key() -> str:
    """API key from env first, secrets file fallback. Never logged."""
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(DEEPINFRA_KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def resolve_lane() -> tuple[str, str, dict[str, str], str]:
    """Return ``(provider, base_url, headers, model)`` for the bulk lane.

    Raises RuntimeError when ``EMBED_PROVIDER=deepinfra`` but no key is
    available — a misconfigured bulk lane must fail loudly instead of
    silently degrading to the slow local server (that failure mode caused
    the multi-day ingest grind on 2026-09-14).
    """
    provider = os.getenv("EMBED_PROVIDER", "").strip().lower()
    key = _deepinfra_key()
    if not provider:
        provider = "deepinfra" if key else "local"
    if provider == "deepinfra":
        if not key:
            raise RuntimeError(
                "EMBED_PROVIDER=deepinfra but no key: set DEEPINFRA_API_KEY or "
                f"DEEPINFRA_KEY_FILE (current: {DEEPINFRA_KEY_FILE})"
            )
        return (
            "deepinfra",
            DEEPINFRA_EMBED_URL,
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            DEEPINFRA_EMBED_MODEL,
        )
    if provider == "local":
        return (
            "local",
            QWEN_EMBED_URL,
            {"Content-Type": "application/json"},
            QWEN_EMBED_MODEL_NAME,
        )
    raise RuntimeError(f"EMBED_PROVIDER={provider!r} unknown (use deepinfra|local)")


async def embed_batch(
    texts: list[str], *, client: httpx.AsyncClient | None = None
) -> list[list[float] | None]:
    """Embed *texts* in one request on the resolved bulk lane.

    Returns one vector per input (None for a failed/malformed entry),
    aligned with input order.  Never raises for HTTP failures.
    """
    if not texts:
        return []
    try:
        provider, base, headers, model = resolve_lane()
    except RuntimeError as exc:
        logger.error("embed_lane_misconfigured: %s", exc)
        return [None] * len(texts)
    payload = {"model": model, "input": [t[:8000] for t in texts]}
    own_client = client is None
    http = client or httpx.AsyncClient()
    try:
        r = await http.post(
            f"{base}/embeddings",
            json=payload,
            headers=headers,
            timeout=_TIMEOUT[provider],
        )
        if r.status_code != 200:
            logger.warning(
                "embed_http_fail lane=%s status=%d body=%s",
                provider,
                r.status_code,
                r.text[:200],
            )
            return [None] * len(texts)
        items = sorted(r.json().get("data", []), key=lambda i: i.get("index", 0))
        if len(items) != len(texts):
            logger.warning(
                "embed_shape_mismatch lane=%s got=%d want=%d", provider, len(items), len(texts)
            )
            return [None] * len(texts)
        out: list[list[float] | None] = []
        for item in items:
            vec = item.get("embedding", [])
            if isinstance(vec, list) and len(vec) == EMBED_DIM:
                out.append([float(x) for x in vec])
            else:
                got = len(vec) if isinstance(vec, list) else type(vec).__name__
                logger.warning(
                    "embed_dim_mismatch lane=%s got=%s want=%d", provider, got, EMBED_DIM
                )
                out.append(None)
        return out
    except Exception as exc:  # noqa: BLE001 — ingest lanes must degrade, not crash
        logger.warning("embed_fail lane=%s err=%s", provider, str(exc)[:200])
        return [None] * len(texts)
    finally:
        if own_client:
            await http.aclose()
