"""Per-provider embedding calls: key lookup, chain order, HTTP call."""

from __future__ import annotations

import os

import httpx

from rugbrain.dim_policy import normalize_dims
from rugbrain.embedder_config import _KEYS, _PROVIDERS, DEFAULT_ORDER, TIMEOUT, logger


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
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            resp = await client.post(
                base,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "input": texts},
            )
        if resp.status_code != 200:
            logger.warning("embed_%s_status=%d", provider, resp.status_code)
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

