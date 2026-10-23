"""PII Scrubber — Presidio-powered redaction for RAG ingestion.

Integrates with the local Presidio service (``/opt/presidio/presidio_service.py``,
port 8090) to redact crypto wallet addresses, email addresses, API keys, and
other PII before text is embedded and stored in Qdrant.

Fail-open: if Presidio is unreachable, the original text passes through
unmodified so RAG ingestion never blocks on PII scrubbing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

PRESIDIO_URL = os.getenv("PRESIDIO_URL", "http://127.0.0.1:8090").rstrip("/")
PRESIDIO_TIMEOUT = float(os.getenv("PRESIDIO_TIMEOUT", "3.0"))
_PRESIDIO_CACHE: dict[str, bool] = {}  # simple in-memory cache


async def _presidio_available() -> bool:
    """Quick probe — is Presidio reachable? Cached for 30s."""
    import time

    now = time.monotonic()
    cached = _PRESIDIO_CACHE.get("available")
    cached_at = _PRESIDIO_CACHE.get("checked_at", 0)
    if cached is not None and (now - cached_at) < 30.0:
        return cached
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{PRESIDIO_URL}/health")
            ok = r.status_code == 200
    except Exception as exc:
        log.debug("presidio_probe_failed url=%s err=%s", PRESIDIO_URL, exc)
        ok = False
    _PRESIDIO_CACHE["available"] = ok
    _PRESIDIO_CACHE["checked_at"] = now  # type: ignore[assignment]
    return ok


async def scrub_text(text: str, language: str = "en") -> str:
    """Redact PII from *text* via Presidio.

    Returns the redacted text with wallet addresses, emails, API keys, etc.
    replaced with placeholder strings. If Presidio is unreachable, returns
    the original text unmodified (fail-open).
    """
    if not text or not text.strip():
        return text

    if not await _presidio_available():
        log.debug("pii_scrubber_unavailable — passing through unredacted")
        return text

    try:
        async with httpx.AsyncClient(timeout=PRESIDIO_TIMEOUT) as client:
            r = await client.post(
                f"{PRESIDIO_URL}/redact",
                json={"text": text, "language": language},
            )
            if r.status_code == 200:
                data = r.json()
                redacted = str(data.get("redacted", text))
                if data.get("pii_found", 0) > 0:
                    log.debug(
                        "pii_redacted count=%d text_len=%d",
                        data["pii_found"],
                        len(text),
                    )
                return redacted
            else:
                log.warning("pii_scrubber_http_%d", r.status_code)
                return text
    except Exception as exc:
        log.warning("pii_scrubber_error %s", str(exc)[:100])
        return text


async def scrub_batch(texts: list[str], language: str = "en") -> list[str]:
    """Redact PII from a batch of texts via Presidio's batch endpoint."""
    if not texts:
        return texts

    if not await _presidio_available():
        return texts

    try:
        async with httpx.AsyncClient(timeout=PRESIDIO_TIMEOUT * 2) as client:
            r = await client.post(
                f"{PRESIDIO_URL}/redact/batch",
                json={"texts": texts, "language": language},
            )
            if r.status_code == 200:
                data = r.json()
                return cast(list[str], data.get("results", texts))
            return texts
    except Exception as exc:
        log.warning("pii_scrub_batch_failed texts=%d err=%s", len(texts), str(exc)[:100])
        return texts


async def analyze_text(text: str, language: str = "en") -> list[dict[str, Any]]:
    """Analyze text for PII without redacting.

    Returns a list of entity dicts: ``[{"entity_type": str, "start": int,
    "end": int, "score": float}]``. Empty list if Presidio is unreachable.
    Normalizes Presidio's response (handles both ``type`` and ``entity_type`` keys).
    """
    if not text or not text.strip():
        return []

    if not await _presidio_available():
        return []

    try:
        async with httpx.AsyncClient(timeout=PRESIDIO_TIMEOUT) as client:
            r = await client.post(
                f"{PRESIDIO_URL}/analyze",
                json={"text": text, "language": language},
            )
            if r.status_code == 200:
                entities = cast(list[dict[str, Any]], r.json())
                # Normalize: some Presidio versions return "type" instead of "entity_type"
                for e in entities:
                    et = e.get("entity_type") or e.get("type")
                    if et and "entity_type" not in e:
                        e["entity_type"] = et
                return entities
            return []
    except Exception as exc:
        log.warning("pii_analyze_failed err=%s", str(exc)[:100])
        return []
