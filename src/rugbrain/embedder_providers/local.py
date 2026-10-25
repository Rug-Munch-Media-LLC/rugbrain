"""Local llama.cpp embedding rung (Qwen3-Embedding-8B, 4096-d, CPU).

The lane is slow and single-slot: one cold request measured ~141 s. So this
rung batches inputs (``EMBED_LOCAL_BATCH``, default 16 per request), waits up
to ``EMBED_LOCAL_TIMEOUT`` seconds per request (default 180) and retries once
per batch. It never raises — the caller's chain decides what happens next.
"""

from __future__ import annotations

import os

import httpx

from rugbrain.embedder_config import logger

ENDPOINT = os.getenv("EMBED_LOCAL_URL", "http://127.0.0.1:8024/v1/embeddings")
MODEL_ID = os.getenv("EMBED_LOCAL_MODEL", "qwen3-embedding-8b")
KEY_ENV = "LLAMA_EMBED_KEY"
_SRV = "/srv"
KEY_FILES = (f"{_SRV}/secrets/embed-lane.env", f"{_SRV}/models/embed-lane.env")
TIMEOUT = float(os.getenv("EMBED_LOCAL_TIMEOUT", "180"))
BATCH = max(1, int(os.getenv("EMBED_LOCAL_BATCH", "16")))

LAST_STATUS: dict[str, str] = {}
_KEY: list[str] = []


def _read_env_key(path: str) -> str:
    """Return ``KEY_ENV``'s value from an env-format file, or ""."""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                name, _, raw = line.partition("=")
                if name.strip() == KEY_ENV:
                    return raw.strip().strip("\"'")
    except OSError:
        return ""
    return ""


def local_key() -> str:
    """Credential from the FILE first, then the env.

    A stale export must never shadow the secrets file: the fleet secrets copy
    is canonical, the model-directory copy is the older location, and
    ``LLAMA_EMBED_KEY`` in the environment is last.
    """
    if _KEY:
        return _KEY[0]
    value = ""
    for path in KEY_FILES:
        value = _read_env_key(path)
        if value:
            break
    if not value:
        value = os.getenv(KEY_ENV, "").strip()
    _KEY.append(value)
    return value


async def embed_local(texts: list[str]) -> list[list[float]] | None:
    """Embed *texts* in batches of :data:`BATCH`; one retry per batch."""
    key = local_key()
    if not key:
        LAST_STATUS["local"] = "no_key"
        return None
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for start in range(0, len(texts), BATCH):
            chunk = texts[start : start + BATCH]
            got = None
            for attempt in (1, 2):
                try:
                    resp = await client.post(
                        ENDPOINT,
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": MODEL_ID, "input": chunk},
                    )
                except Exception as exc:
                    LAST_STATUS["local"] = f"transport:{type(exc).__name__}"
                    logger.warning(
                        "embed_local_transport attempt=%d err=%s", attempt, str(exc)[:100]
                    )
                    continue
                if resp.status_code != 200:
                    LAST_STATUS["local"] = f"status={resp.status_code}"
                    logger.warning("embed_local_status=%d attempt=%d", resp.status_code, attempt)
                    continue
                items = resp.json().get("data") or []
                if len(items) != len(chunk):
                    LAST_STATUS["local"] = f"count={len(items)}/{len(chunk)}"
                    logger.warning("embed_local_count got=%d want=%d", len(items), len(chunk))
                    continue
                got = [e.get("embedding") for e in sorted(items, key=lambda i: i.get("index", 0))]
                break
            if got is None or any(not isinstance(v, list) for v in got):
                LAST_STATUS["local"] = LAST_STATUS.get("local", "bad_shape")
                return None
            vectors.extend(got)
    LAST_STATUS["local"] = f"ok batch={BATCH}"
    return vectors
