"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The two outward stages: publish the site, then hand Telegram its card.

Publication goes through the store seam Lane D owns (``daily_rug_publish``
re-exports ``default_store``; a Ghost adapter replaces it without the pipeline
changing). The store upserts by slug, so a retried publish bumps the revision
instead of minting a second URL.

Delivery is one HTTP call to the channel bridge (Lane E owns the bot; this
module owns neither the token nor the chat id). The bridge receives a complete
payload — method, text, image, idempotency key — so the decision "photo or
text" is made and recorded here, where the acceptance harness can assert it.

Image rule: a real story image is used when the document carries one. The
site-wide Open Graph default is NOT a story image, so a document that only has
the default falls back to a text post rather than shipping a generic card for
every edition.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from rugbrain.content_daily import editions

BRIDGE_ENV = "RUG_CONTENT_TELEGRAM_URL"
BRIDGE_TIMEOUT = 30.0


class HttpStatus(Exception):
    """A bridge/publisher HTTP failure that carries the retry vocabulary."""

    def __init__(self, status: int, body: str, retry_after: float | None = None) -> None:
        super().__init__(f"http {status}: {body[:200]}")
        self.status = status
        self.body = body[:300]
        self.retry_after = retry_after


class DeliveryNotConfigured(Exception):
    """No bridge URL is configured; the stage cannot even attempt a send."""


def _default_og() -> str:
    """The site-wide Open Graph default — not a story image."""
    try:
        from rugbrain.content_style import load_style  # noqa: PLC0415 - avoids import cost

        return str(load_style()["open_graph"]["image"])
    except (KeyError, OSError, ValueError):
        return ""


def image_for(doc: dict[str, Any]) -> str | None:
    """The story image, or None when only the site default exists."""
    explicit = str(doc.get("image_url") or "").strip()
    if explicit:
        return explicit
    candidate = str((doc.get("open_graph") or {}).get("image") or "").strip()
    if candidate and candidate != _default_og():
        return candidate
    return None


def telegram_payload(
    edition_key: str, doc: dict[str, Any], published: dict[str, Any]
) -> dict[str, Any]:
    """The exact body the bridge receives — asserted by the acceptance harness."""
    edition = editions.get(edition_key)
    url = str(published.get("url") or doc.get("url") or "")
    text = str((doc.get("telegram") or {}).get("text") or doc.get("dek") or "").strip()
    if url and url not in text:
        text = f"{text}\n\n{url}"
    image = image_for(doc)
    return {
        "channel": edition.channel,
        "edition": edition.key,
        "run_key": published.get("run_key") or "",
        "method": "sendPhoto" if image else "sendMessage",
        "text": text,
        "image_url": image,
        "url": url,
        "parse_mode": "HTML",
        "idempotency_key": f"{published.get('run_key') or edition.key}:tg:{edition.channel}",
    }


async def publish_document(doc: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Publish through Lane D's store seam; a dry run writes nothing."""
    title = str(doc.get("edition_title") or doc.get("title") or "")
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "slug": doc.get("slug"),
            "url": doc.get("url"),
            "revision": 0,
            "title": title,
        }
    from rugbrain.daily_rug_publish import default_store  # noqa: PLC0415 - Lane D seam

    store = default_store()
    record = store.upsert(doc)
    return {
        "ok": True,
        "dry_run": False,
        "slug": record.get("slug"),
        "url": record.get("url"),
        "revision": record.get("revision"),
        "title": title or record.get("title"),
    }


async def deliver_telegram(
    edition_key: str, doc: dict[str, Any], published: dict[str, Any], dry_run: bool
) -> dict[str, Any]:
    """Send the card to the channel bridge; the retry policy lives in the contract."""
    payload = telegram_payload(edition_key, doc, published)
    if dry_run:
        return {"ok": True, "dry_run": True, "message_ids": [], "payload": payload}

    bridge = os.getenv(BRIDGE_ENV, "").strip()
    if not bridge:
        raise DeliveryNotConfigured(f"{BRIDGE_ENV} is not set; refused to guess a channel")
    async with httpx.AsyncClient(timeout=BRIDGE_TIMEOUT) as client:
        response = await client.post(bridge, json=payload)
    if response.status_code >= 400:
        raw = response.headers.get("Retry-After", "")
        retry_after = float(raw) if raw.strip().replace(".", "", 1).isdigit() else None
        raise HttpStatus(response.status_code, response.text, retry_after)
    body = response.json() or {}
    ids = body.get("message_ids")
    if ids is None and body.get("message_id") is not None:
        ids = [body["message_id"]]
    return {
        "ok": True,
        "dry_run": False,
        "message_ids": list(ids or []),
        "method": payload["method"],
        "payload": payload,
    }


__all__ = [
    "BRIDGE_ENV",
    "DeliveryNotConfigured",
    "HttpStatus",
    "deliver_telegram",
    "image_for",
    "publish_document",
    "telegram_payload",
]
