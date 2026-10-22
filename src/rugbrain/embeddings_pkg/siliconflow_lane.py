"""SiliconFlow 8B embedding lane — the HEAD of the embed chain.

``SILICONFLOW_ENABLED`` still defaults to OFF in code (an unkeyed lane must
never be reached by accident), but the deployed env sets it to 1 and
``embeddings.AUTO_CHAIN`` puts this lane FIRST: it is the only hosted lane with a
live 200-at-4096 credential. DeepInfra is the DEAD tail rung (402), not a
primary. Same ``Qwen/Qwen3-Embedding-8B`` family at 4096d, so a vector from this
lane is index-compatible with the ``*_8b`` collections.

**HOST MATTERS: ``.cn`` and ``.com`` are DIFFERENT hosts with DIFFERENT
credentials.** Measured 2026-10-22 from Talos: the key in
``/srv/secrets/siliconflow_api_key`` returns HTTP 200, ``dim=4096``,
``model=Qwen/Qwen3-Embedding-8B`` against ``api.siliconflow.com``, and HTTP 401
``code=30014 Token is invalid`` against ``api.siliconflow.cn``. The old note
here (blocked, token invalid) was measured against ``.cn`` and wrongly retired
a healthy 8B/4096d provider — the exact dimension-correct backup this chain
needs. Never point the default back at ``.cn``.
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
    "SILICONFLOW_EMBED_URL", "https://api.siliconflow.com/v1/embeddings"
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
    if not SILICONFLOW_ENABLED:
        # 2026-10-22: this used to return None with NO log at all, so a
        # config-gated-OFF rung looked identical to a working one during 503
        # triage. That silence is why the chain appeared to have a backup
        # provider it never actually called.
        logger.warning("siliconflow_disabled url=%s", SILICONFLOW_URL)
        return None
    if not text.strip():
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
            logger.warning(
                "siliconflow_status=%d url=%s body=%s",
                resp.status_code,
                SILICONFLOW_URL,
                resp.text[:160],
            )
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
