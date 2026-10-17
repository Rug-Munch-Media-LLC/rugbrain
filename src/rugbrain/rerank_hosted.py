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

# ── Intelligent provider fallback (2026-10-17) ────────────────────────────
# Operator rule: "never run out of credit". Order: DeepInfra -> SiliconFlow
# -> OpenRouter (backup account). Every lane serves the same Qwen3-Reranker-8B
# so scores stay comparable; a lane that errors is skipped, never fatal.
# On 2026-10-17 DeepInfra returned 402 (overdue invoices) and, because
# hosted_enabled() gated on the DeepInfra key alone, ALL reranking silently
# stopped. This chain is the fix.
# `request_form`: deepinfra's inference API takes "queries": [str]; the
# OpenAI-compatible lanes (siliconflow, openrouter) take "query": str.
# Verified 2026-10-17 against all three live endpoints.
RERANK_LANES: tuple[dict[str, str], ...] = (
    {
        "name": "deepinfra",
        "url": RERANK_HOSTED_URL,
        "model": RERANK_HOSTED_MODEL,
        "key_env": "DEEPINFRA_API_KEY",
        "request_form": "queries",
    },
    {
        "name": "siliconflow",
        "url": os.getenv(
            "SILICONFLOW_RERANK_URL", "https://api.siliconflow.com/v1/rerank"
        ),
        "model": os.getenv("SILICONFLOW_RERANK_MODEL", "Qwen/Qwen3-Reranker-8B"),
        "key_env": "SILICONFLOW_API_KEY",
        "request_form": "query",
    },
    {
        "name": "openrouter",
        "url": os.getenv(
            "OPENROUTER_RERANK_URL", "https://openrouter.ai/api/v1/rerank"
        ),
        "model": os.getenv("OPENROUTER_RERANK_MODEL", "qwen/qwen3-reranker-8b"),
        "key_env": "OPENROUTER_API_KEY",
        "request_form": "query",
    },
)

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


def _lane_key(env_var: str) -> str:
    """Key for a rerank lane. Only DeepInfra has the file-fallback chain."""
    if env_var == "DEEPINFRA_API_KEY":
        return _deepinfra_key()
    return os.getenv(env_var, "").strip()


def hosted_enabled() -> bool:
    """True when ANY rerank lane has a key (not just DeepInfra)."""
    if os.getenv("RERANK_HOSTED", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return any(_lane_key(lane["key_env"]) for lane in RERANK_LANES)


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
    last_err: str = ""
    for lane in RERANK_LANES:
        key = _lane_key(lane["key_env"])
        if not key:
            continue
        try:
            async with httpx.AsyncClient(timeout=timeout or RERANK_HOSTED_TIMEOUT) as c:
                resp = await c.post(
                    lane["url"],
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        (
                            "queries"
                            if lane.get("request_form") == "queries"
                            else "query"
                        ): (
                            [query[:_HOSTED_DOC_CHARS]]
                            if lane.get("request_form") == "queries"
                            else query[:_HOSTED_DOC_CHARS]
                        ),
                        "documents": [d[:_HOSTED_DOC_CHARS] for d in documents],
                        "model": lane["model"],
                    },
                )
                if resp.status_code != 200:
                    last_err = f"{lane['name']} HTTP {resp.status_code}"
                    logger.warning("rerank_lane_status %s", last_err)
                    continue
                payload = resp.json()
                # DeepInfra returns {"scores": [...]}; SiliconFlow/OpenRouter
                # return {"results": [{"index": i, "relevance_score": f}]}.
                scores = payload.get("scores")
                if scores is None:
                    scores = [
                        (r.get("relevance_score") or 0.0)
                        for r in sorted(
                            payload.get("results") or [],
                            key=lambda r: r.get("index", 0),
                        )
                    ]
            if len(scores) != len(documents):
                last_err = f"{lane['name']} shape got={len(scores)} want={len(documents)}"
                logger.warning("rerank_lane_shape %s", last_err)
                continue
            results = [
                {"index": i, "relevance_score": float(v)} for i, v in enumerate(scores)
            ]
            results.sort(key=lambda r: r["relevance_score"], reverse=True)
            return results[:top_n] if top_n and top_n > 0 else results
        except Exception as exc:
            last_err = f"{lane['name']} {type(exc).__name__}"
            logger.warning("rerank_lane_failed %s: %s", last_err, str(exc)[:100])
            continue
    logger.warning("rerank_all_lanes_failed: %s", last_err or "no lane had a key")
    return None
