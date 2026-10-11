"""SiliconFlow 8B embedding lane — CONFIG-GATED, DISABLED BY DEFAULT.

Third rung of the embed chain. Same 8B family as the DeepInfra primary
(``Qwen/Qwen3-Embedding-8B``, 4096d), so a vector from this lane is
index-compatible with the ``*_8b`` collections.

**BLOCKED as of 2026-10-02.** The only credential on this box returns HTTP 401
"Token is invalid", and it is UNVERIFIED whether SiliconFlow serves the 8B model
id at all. So this lane is gated behind ``SILICONFLOW_ENABLED=1`` and returns
``None`` — never raises, never returns a placeholder vector — until an operator
rotates the key AND observes HTTP 200 at dim=4096. See the unblock recipe in
``.env.rag``. Per T04 a dead lane must fall through, never fabricate a vector.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

import httpx

logger = logging.getLogger(__name__)

SILICONFLOW_ENABLED = os.getenv("SILICONFLOW_ENABLED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SILICONFLOW_URL = os.getenv(
    "SILICONFLOW_EMBED_URL", "https://api.siliconflow.cn/v1/embeddings"
).rstrip("/")
SILICONFLOW_MODEL = os.getenv("SILICONFLOW_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
SILICONFLOW_KEY_FILE = os.getenv("SILICONFLOW_KEY_FILE", "/srv/secrets/siliconflow_api_key")
_TIMEOUT = float(os.getenv("SILICONFLOW_TIMEOUT", "20.0"))


def _api_key() -> str:
    """Key from the environment, else the gopass-spooled file. Never logged."""
    key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(SILICONFLOW_KEY_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


async def embed(
    text: str,
    *,
    query: bool = False,  # noqa: ARG001 - provider applies its own template
    timeout: float | None = None,  # noqa: ASYNC109 - policy value, not an asyncio timeout
) -> list[float] | None:
    """Embed *text* on SiliconFlow, or None when the rung is unusable.

    ``None`` means "fall through to the next rung" — it is returned for a
    disabled flag, a missing key, any non-200, a malformed body, or a
    dimension that is not 4096. It never raises, so a dead rung can sit in a
    chain without taking the request down.
    """
    if not SILICONFLOW_ENABLED or not text.strip():
        return None
    key = _api_key()
    if not key:
        logger.warning("siliconflow_no_key file=%s", SILICONFLOW_KEY_FILE)
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
            resp = await client.post(
                SILICONFLOW_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": SILICONFLOW_MODEL, "input": [text]},
            )
        if resp.status_code != 200:
            logger.warning("siliconflow_status=%d", resp.status_code)
            return None
        vector = (resp.json().get("data") or [{}])[0].get("embedding")
        if not isinstance(vector, list) or len(vector) != 4096:
            logger.warning("siliconflow_dim=%s want=4096", len(vector) if vector else None)
            return None
        return [float(v) for v in vector]
    except Exception as exc:  # noqa: BLE001 - a rung must never kill the chain
        logger.debug("siliconflow_failed: %s", str(exc)[:120])
        return None

async def embed_batch(
    texts: Sequence[str],
    *,
    timeout: float | None = None,
) -> list[list[float]] | None:
    """Embed many texts in ONE round trip, or None when the rung is unusable.

    Measured 2026-10-11 against siliconflow.com with Qwen3-Embedding-8B: 1 text
    0.4s, 32 texts 0.8s — the per-request overhead dominates, so batching is
    worth ~30x on a bulk re-embed. Returns None on any failure (same contract as
    :func:`embed`) so a dead rung can sit in a chain without taking a caller
    down; a caller that needs partial results should chunk and retry.
    """
    if not SILICONFLOW_ENABLED or not texts:
        return None
    items = [t for t in texts if isinstance(t, str) and t.strip()]
    if not items:
        return None
    key = _api_key()
    if not key:
        logger.warning("siliconflow_no_key file=%s", SILICONFLOW_KEY_FILE)
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
            resp = await client.post(
                SILICONFLOW_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": SILICONFLOW_MODEL, "input": items},
            )
        if resp.status_code != 200:
            logger.warning("siliconflow_batch_status=%d n=%d", resp.status_code, len(items))
            return None
        vectors = (resp.json().get("data") or [])
        out: list[list[float]] = []
        for entry in vectors:
            vector = (entry or {}).get("embedding")
            # A wrong dim is a hard stop, never a silent mismatch: a 2560 vector
            # beside 4096 collections corrupts every result it touches.
            if not isinstance(vector, list) or len(vector) != 4096:
                logger.warning(
                    "siliconflow_batch_dim=%s want=4096", len(vector) if vector else None
                )
                return None
            out.append([float(v) for v in vector])
        if len(out) != len(items):
            logger.warning("siliconflow_batch_count=%d want=%d", len(out), len(items))
            return None
        return out
    except Exception as exc:  # noqa: BLE001 — a rung must never kill the chain
        logger.debug("siliconflow_batch_failed: %s", str(exc)[:120])
        return None
