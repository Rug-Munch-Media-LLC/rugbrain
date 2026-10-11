"""Hosted DeepInfra rerank rung — the live lane for interactive rerank.

DeepInfra hosts the SAME ``Qwen/Qwen3-Reranker-8B`` family as the local
llama.cpp server (:8003) and answers a full 20-40 doc pool in ~0.5-2s —
where the CPU server needs 30s+ on 2000-char pools.  Policy (2026-09-14,
fleet doctrine — see also contextcrypto rerank.py):

  * interactive/live rerank  -> hosted first, local fallback;
  * batch/deep/big-corpus    -> local first (free), hosted fallback;
  * the local 8B remains the fallback everywhere and the primary lane for
    news ranking, deep/big-corpus analysis, alerts and KOL reranks.

Set ``RERANK_HOSTED=0`` to disable the hosted rung entirely (local-only).

Key resolution: ``DEEPINFRA_API_KEY`` env, else the first readable file at
``DEEPINFRA_KEY_FILE`` / ``/srv/secrets/deepinfra_api_key`` /
``/run/secrets/deepinfra_api_key``.  Never logged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RERANK_HOSTED_URL = os.getenv(
    "RERANK_HOSTED_URL",
    "https://api.deepinfra.com/v1/inference/Qwen/Qwen3-Reranker-8B",
)
RERANK_HOSTED_MODEL = os.getenv("RERANK_HOSTED_MODEL", "Qwen/Qwen3-Reranker-8B")
RERANK_HOSTED_TIMEOUT = float(os.getenv("RERANK_HOSTED_TIMEOUT", "6.0"))
RERANK_HOSTED_CAP = int(os.getenv("RERANK_HOSTED_CAP", "40"))
_HOSTED_DOC_CHARS = int(os.getenv("RERANK_HOSTED_DOC_CHARS", "2000"))

_KEY_CACHE: str = ""
_KEY_LOADED = False


def _deepinfra_key() -> str:
    """API key: env first, then file fallbacks (host + container mounts)."""
    global _KEY_CACHE, _KEY_LOADED
    if _KEY_LOADED:
        return _KEY_CACHE
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if not key:
        candidates = []
        explicit = os.getenv("DEEPINFRA_KEY_FILE", "").strip()
        if explicit:
            candidates.append(explicit)
        candidates += ["/srv/secrets/deepinfra_api_key", "/run/secrets/deepinfra_api_key"]
        for path in candidates:
            try:
                with open(path, encoding="utf-8") as fh:
                    key = fh.read().strip()
                if key:
                    break
            except OSError:
                continue
    _KEY_CACHE = key or ""
    _KEY_LOADED = True
    return _KEY_CACHE


def hosted_enabled() -> bool:
    """True when the hosted rerank rung may be used."""
    if os.getenv("RERANK_HOSTED", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(_deepinfra_key())


async def hosted_rerank(
    query: str,
    documents: list[str],
    top_n: int = 0,
    *,
    timeout: float | None = None,  # noqa: ASYNC109 — forwarded to the HTTP client, not an asyncio timeout
) -> list[dict[str, Any]] | None:
    """Score *documents* against *query* on DeepInfra.

    Returns llama.cpp-shaped results ``[{"index": int, "relevance_score":
    float}]`` sorted by score descending (all docs scored; ``top_n > 0``
    truncates).  NEVER raises — ``None`` means "rung unavailable/failed,
    fall through to the next rung".
    """
    if not documents or not hosted_enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout or RERANK_HOSTED_TIMEOUT) as client:
            resp = await client.post(
                RERANK_HOSTED_URL,
                headers={"Authorization": f"Bearer {_deepinfra_key()}"},
                json={
                    "queries": [query[:_HOSTED_DOC_CHARS]],
                    "documents": [d[:_HOSTED_DOC_CHARS] for d in documents],
                },
            )
            if resp.status_code != 200:
                logger.warning("rerank_hosted_status=%d", resp.status_code)
                return None
            scores = resp.json().get("scores") or []
        if len(scores) != len(documents):
            logger.warning("rerank_hosted_shape got=%d want=%d", len(scores), len(documents))
            return None
        results = [{"index": i, "relevance_score": float(s)} for i, s in enumerate(scores)]
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        return results[:top_n] if top_n and top_n > 0 else results
    except Exception as exc:
        logger.debug("rerank_hosted_failed: %s", str(exc)[:120])
        return None
