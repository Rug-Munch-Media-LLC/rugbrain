"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

The single publication path for a Daily Rug long-form.

Generate with citations, refuse thin evidence, upsert the story, hand back the
canonical URL and a Telegram teaser. Whatever the store is (file today, a
Ghost adapter later), the contract is the same:

* ``ok``                 — published, canonical URL returned.
* ``insufficient_evidence`` — nothing written, the reason names the count.
* ``needs_review``       — nothing written, ``style_violations`` lists why.
"""

from __future__ import annotations

from typing import Any

from rugbrain.content_style import load_style
from rugbrain.daily_rug import generate_daily_rug
from rugbrain.daily_rug_store import StoryStore, default_store


def _story(doc: dict[str, Any]) -> dict[str, Any]:
    """The record a story page renders — slug, title, dek, sections, sources, OG."""
    return {
        "slug": doc["slug"],
        "url": doc["url"],
        "status": "published",
        "topic": doc["topic"],
        "title": doc["title"],
        "dek": doc["dek"],
        "date": doc["date"],
        "sections": doc["sections"],
        "sources": doc["sources"],
        "open_graph": doc["open_graph"],
        "cta": doc["cta"],
        "disclaimer": doc["disclaimer"],
        "body_markdown": doc["body_markdown"],
        "distribution": {
            "telegram": {
                "text": doc["telegram"]["text"],
                "chars": doc["telegram"]["chars"],
                "limit": doc["telegram"]["limit"],
                "url": doc["url"],
            }
        },
    }


async def publish_daily_rug(
    topic: str,
    *,
    date: str | None = None,
    search_fn: Any = None,
    writer: Any = None,
    store: StoryStore | None = None,
    style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate and publish one brief; an upsert, never a duplicate URL."""
    rules = style or load_style()
    doc = await generate_daily_rug(
        topic, date=date, search_fn=search_fn, writer=writer, style=rules
    )
    if doc["status"] != "ok":
        return {
            "status": doc["status"],
            "published": False,
            "topic": doc["topic"],
            "date": doc["date"],
            "reason": doc.get("reason", ""),
            "style_violations": doc.get("style_violations", []),
            "slug": doc.get("slug"),
            "url": doc.get("url"),
        }

    record = (store or default_store()).upsert(_story(doc))
    summary = record["distribution"]["telegram"]
    return {
        "status": "ok",
        "published": True,
        "slug": record["slug"],
        "url": record["url"],
        "canonical_url": record["url"],
        "revision": record["revision"],
        "title": record["title"],
        "sources": len(record["sources"]),
        "telegram": {**summary, "under_limit": summary["chars"] < summary["limit"]},
    }
