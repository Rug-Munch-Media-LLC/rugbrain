"""Multi-provider hosted rerank — one chain, three rungs, no single point of failure.

2026-10-11: rerank was DeepInfra-only, so the credit exhaustion took rerank down
with it. DeepInfra also served ``Qwen3-Reranker-8B`` while the rest of the
system runs the 8B — a rerank/decode quality mismatch across lanes.

All three providers serve **Qwen/Qwen3-Reranker-8B** (verified live
2026-10-11: siliconflow.com and openrouter both rank `reentrancy in withdraw`
above an unrelated css helper, and return Cohere-shaped ``results`` with
``relevance_score``). Each rung returns ``None`` on any failure — missing key,
non-200, malformed body, wrong shape — so a dead provider costs a hop, not the
request. The caller keeps the same contract it had: llama.cpp-shaped
``[{"index": int, "relevance_score": float}]`` sorted descending, or ``None``
when every rung is unavailable.

Order is ``RERANK_PROVIDER_ORDER`` (comma separated). Operator decision
2026-10-11: DeepInfra is PRIMARY (verified live: embed + rerank both answer),
SiliconFlow is the first backup, OpenRouter the last. A rung with no
credential simply no-ops and the chain falls through to the next one.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MODEL = os.getenv("RERANK_MODEL_ID", "Qwen/Qwen3-Reranker-8B")
TIMEOUT = float(os.getenv("RERANK_HOSTED_TIMEOUT", "30.0"))
DOC_CHARS = int(os.getenv("RERANK_HOSTED_DOC_CHARS", "2000"))
DEFAULT_ORDER = ("deepinfra", "siliconflow", "openrouter", "openrouter2")

#: provider -> (base url, model id, credential env var, credential file)
_PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "siliconflow": (
        "https://api.siliconflow.com/v1/rerank",
        "Qwen/Qwen3-Reranker-8B",
        "SILICONFLOW_API_KEY",
        "/srv/secrets/siliconflow_api_key",
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1/rerank",
        "qwen/qwen3-reranker-8b",
        "OPENROUTER_API_KEY",
        "/srv/secrets/openrouter_account1_api_key",
    ),
    "openrouter2": (
        "https://openrouter.ai/api/v1/rerank",
        "qwen/qwen3-reranker-8b",
        "OPENROUTER_BACKUP_API_KEY",
        "/srv/secrets/openrouter_account2_api_key",
    ),
    "deepinfra": (
        "https://api.deepinfra.com/v1/inference/Qwen/Qwen3-Reranker-8B",
        "Qwen/Qwen3-Reranker-8B",
        "DEEPINFRA_API_KEY",
        "/srv/secrets/deepinfra_api_key",
    ),
}

_KEYS: dict[str, str] = {}


def _key(provider: str) -> str:
    """Credential for a provider: env first, then the spooled key file."""
    if provider in _KEYS:
        return _KEYS[provider]
    _, _, env_name, key_file = _PROVIDERS[provider]
    value = os.getenv(env_name, "").strip()
    if not value:
        try:
            with open(key_file, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            value = ""
    _KEYS[provider] = value
    return value


def _order() -> tuple[str, ...]:
    raw = os.getenv("RERANK_PROVIDER_ORDER", "")
    names = [n.strip().lower() for n in raw.split(",") if n.strip().lower() in _PROVIDERS]
    return tuple(names) + tuple(p for p in DEFAULT_ORDER if p not in names)


async def _call(
    provider: str, query: str, documents: list[str], timeout: float | None
) -> list[dict[str, Any]] | None:
    base, model, _, _ = _PROVIDERS[provider]
    body_query = query[:DOC_CHARS]
    docs = [d[:DOC_CHARS] for d in documents]
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            if provider == "deepinfra":
                # DeepInfra inference protocol: queries/documents -> scores[]
                payload: dict[str, Any] = {"queries": [body_query], "documents": docs}
            else:
                # Cohere protocol (siliconflow + openrouter): query/documents
                payload = {"model": model, "query": body_query, "documents": docs}
            resp = await client.post(
                base, headers={"Authorization": f"Bearer {_key(provider)}"}, json=payload
            )
        if resp.status_code != 200:
            logger.warning("rerank_%s_status=%d", provider, resp.status_code)
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — a rung must never kill the chain
        logger.debug("rerank_%s_failed: %s", provider, str(exc)[:120])
        return None

    if provider == "deepinfra":
        scores = data.get("scores") or []
        if len(scores) != len(documents):
            logger.warning("rerank_%s_shape got=%d want=%d", provider, len(scores), len(documents))
            return None
        results = [{"index": i, "relevance_score": float(s)} for i, s in enumerate(scores)]
    else:
        entries = data.get("results") or []
        if len(entries) != len(documents):
            logger.warning("rerank_%s_shape got=%d want=%d", provider, len(entries), len(documents))
            return None
        results = []
        for entry in entries:
            index = entry.get("index")
            score = entry.get("relevance_score")
            if not isinstance(index, int) or score is None:
                logger.warning("rerank_%s_shape bad_entry=%s", provider, str(entry)[:80])
                return None
            results.append({"index": index, "relevance_score": float(score)})
    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results


async def hosted_rerank(
    query: str,
    documents: list[str],
    top_n: int = 0,
    *,
    timeout: float | None = None,  # noqa: ASYNC109 — forwarded to the HTTP client, not an asyncio timeout
) -> list[dict[str, Any]] | None:
    """Score *documents* against *query* on the first healthy hosted provider.

    Returns llama.cpp-shaped ``[{"index": int, "relevance_score": float}]``
    sorted by score descending (all documents scored; ``top_n > 0`` truncates),
    or ``None`` when every rung is unavailable — the same contract the
    DeepInfra-only lane had, so no caller had to change.
    """
    if not documents:
        return None
    for provider in _order():
        if not _key(provider):
            continue
        results = await _call(provider, query, documents, timeout)
        if results is not None:
            logger.info("rerank_lane=%s docs=%d", provider, len(documents))
            return results[:top_n] if top_n and top_n > 0 else results
    return None


def hosted_enabled() -> bool:
    """Whether any hosted rung has a credential (drives the caller-side flag)."""
    return any(_key(provider) for provider in _PROVIDERS)
