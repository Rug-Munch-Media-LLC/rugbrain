"""Per-provider embedding calls: key lookup, chain order, HTTP call."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from rugbrain.dim_policy import normalize_dims
from rugbrain.embedder_config import _KEYS, _PROVIDERS, DEFAULT_ORDER, TIMEOUT, logger
from rugbrain.embedder_providers.local import LAST_STATUS as _LOCAL_STATUS
from rugbrain.embedder_providers.local import embed_local, local_key

#: provider -> human-readable outcome of the most recent attempt
LAST_FAILURE: dict[str, str] = {}


def _key(provider: str) -> str:
    # FILE-FIRST: the secrets file is canonical; an env var may be stale and
    # must never shadow a working key (2026-10-25: stale env key caused every
    # lane to report the provider as dead while the file key worked).
    _cfg = _PROVIDERS.get(provider)
    if _cfg and len(_cfg) > 3 and _cfg[3]:
        try:
            _v = Path(_cfg[3]).read_text().strip()
            if _v:
                return _v
        except OSError:
            pass
    """Credential for a provider: env first, then the spooled key file."""
    if provider == "local":
        return local_key()
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
    raw = os.getenv("EMBED_PROVIDER_ORDER", "")
    names = [n.strip().lower() for n in raw.split(",") if n.strip().lower() in _PROVIDERS]
    return tuple(names) + tuple(p for p in DEFAULT_ORDER if p not in names)


async def _call(
    provider: str, texts: list[str], timeout: float | None  # noqa: ASYNC109 — forwarded to HTTP client
) -> list[list[float]] | None:
    """Call a single provider for embeddings. Returns list of vectors or None."""
    base, model, _, _ = _PROVIDERS[provider]
    key = _key(provider)
    if not key:
        logger.debug("embed_%s_skipped no_api_key", provider)
        LAST_FAILURE[provider] = "no_key"
        return None
    if provider == "local":
        vectors = await embed_local(texts)
        LAST_FAILURE[provider] = _LOCAL_STATUS.get("local", "unknown")
        if vectors is None:
            return None
        return [normalize_dims(v, 4096) for v in vectors]
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            resp = await client.post(
                base,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "input": texts},
            )
        if resp.status_code != 200:
            logger.warning("embed_%s_status=%d", provider, resp.status_code)
            LAST_FAILURE[provider] = f"status={resp.status_code}"
            return None
        items = (resp.json().get("data") or [])
        if len(items) != len(texts):
            logger.warning("embed_%s_count got=%d want=%d", provider, len(items), len(texts))
            return None
        vectors = []
        for entry in sorted(items, key=lambda i: i.get("index", 0)):
            vec = entry.get("embedding")
            if not isinstance(vec, list):
                logger.warning("embed_%s_shape bad_entry=%s", provider, str(entry)[:80])
                return None
            vectors.append(vec)
    except Exception as exc:  # a rung must never kill the chain
        logger.debug("embed_%s_failed: %s", provider, str(exc)[:120])
        return None

    # Normalize dimensions (rejects wrong dims per dim_policy)
    normalized = []
    for i, vec in enumerate(vectors):
        try:
            normalized.append(normalize_dims(vec, 4096))
        except ValueError as exc:
            logger.warning("embed_%s_dim_mismatch idx=%d: %s", provider, i, exc)
            return None
    return normalized

