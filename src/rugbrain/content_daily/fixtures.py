"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

Deterministic offline dependencies: a whole edition with no network and no disk.

Two callers share this module, which is why it exists at all: the acceptance
harness (``tests/test_content_daily_acceptance.py``) and ``daily --offline``,
the smoke mode an operator runs to prove the unit, the venv and the scheduler
wiring without touching a source, the site or the channel.

The document is not a stub. It is shaped like the real one and it is checked by
the real editor (:func:`rugbrain.content_daily.adapters.edit_document`), so a
change to the publication rules fails this fixture instead of passing it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from rugbrain.content_daily import adapters, delivery, editions
from rugbrain.content_daily.deps import Deps

#: Three sources, three publishers — the validator's independence floor.
SOURCES: tuple[dict[str, Any], ...] = (
    {
        "id": 1,
        "title": "Velodrome router drain",
        "publisher": "Rekt News",
        "url": "https://rekt.news/velodrome-router",
        "retrieved_at": "2026-10-29T04:20:00Z",
    },
    {
        "id": 2,
        "title": "Optimism finality",
        "publisher": "L2Beat",
        "url": "https://l2beat.com/scaling/optimism",
        "retrieved_at": "2026-10-29T04:25:00Z",
    },
    {
        "id": 3,
        "title": "Bridge flow",
        "publisher": "Nansen",
        "url": "https://nansen.ai/research/bridge-flow",
        "retrieved_at": "2026-10-29T04:30:00Z",
    },
)

HEADINGS: tuple[str, ...] = (
    "Overnight risk tape",
    "Rugs and exploits",
    "Wallets that moved size",
    "What the desk watches today",
)

BODIES: tuple[str, ...] = (
    "An attacker used a stale router approval to drain 2.4 million dollars from "
    "three Velodrome pools on Optimism at 04:12 UTC [1].",
    "Sequencer finality on Optimism lagged 41 minutes across the same window, and "
    "net bridge outflow reached 18,400 ETH overnight [2][3].",
    "The desk's read is that the drain stayed inside three pools; no fourth pool "
    "was touched by 21:00 UTC [1][2].",
    "The next scheduled item is a 4.2% supply unlock on October 30, 2026, which "
    "the desk will mark if the tape moves early [3].",
)

#: One story per edition, plus one copy for the dedupe stage to collapse.
STORIES: tuple[dict[str, Any], ...] = (
    {
        "title": "Velodrome pools drained after router approval bug",
        "content": "An attacker drained three Velodrome pools on Optimism using a stale router approval.",
        "source": "https://rekt.news/velodrome-router",
        "category": "security",
        "kind": "story",
        "importance": 0.92,
    },
    {
        "title": "Velodrome pools drained after router approval bug",
        "content": "An attacker drained three Velodrome pools on Optimism using a stale router approval.",
        "source": "https://t.me/rektnews/9931",
        "category": "security",
        "kind": "story",
        "importance": 0.71,
    },
    {
        "title": "Optimism finality lagged 41 minutes",
        "content": "Sequencer finality lagged across the drain window on Optimism.",
        "source": "https://l2beat.com/scaling/optimism",
        "category": "news",
        "kind": "story",
        "importance": 0.88,
    },
    {
        "title": "Bridge outflow reaches 18,400 ETH",
        "content": "Net bridge outflow reached the largest single-day move since May 2026.",
        "source": "https://nansen.ai/research/bridge-flow",
        "category": "onchain",
        "kind": "story",
        "importance": 0.79,
    },
)


def document(edition_key: str, day: str) -> dict[str, Any]:
    """The edition document: validator-clean, with no story image."""
    edition = editions.get(edition_key)
    slug = f"daily-rug-{day}-{edition_key}"
    sections = [{"heading": HEADINGS[i], "body": body} for i, body in enumerate(BODIES)]
    text = f"{edition.title}: {BODIES[0][:90]}"
    return {
        "status": "ok",
        "topic": edition.topic,
        "slug": slug,
        "url": f"https://rugmunch.io/story/{slug}",
        "title": edition.title,
        "edition_title": edition.title_for(day),
        "dek": "A stale router approval and a sequencer delay landed in the same window.",
        "date": day,
        "paragraphs": list(BODIES[: edition.min_paragraphs]),
        "sections": sections,
        "sources": [dict(row) for row in SOURCES],
        "tokens": [],
        "charts": [],
        "cta": {"label": "Check any token before you sign", "url": "https://rugmunch.io/scan"},
        "disclaimer": "Analysis, not financial advice. Every figure links to its source.",
        "body_markdown": "\n\n".join(BODIES),
        "open_graph": {},
        "telegram": {"text": text, "chars": len(text), "limit": 4096},
        "image_url": "",
        "writer": {"mode": "offline-fixture"},
    }


async def _collect(edition_key: str) -> list[dict[str, Any]]:
    """The fixed candidate pool; the edition only decides how much it takes."""
    del edition_key
    return [dict(story) for story in STORIES]


def _score(story: dict[str, Any]) -> float:
    """The fixture's own importance, so ranking is deterministic."""
    return float(story.get("importance") or 0.0)


async def _write(edition_key: str, stories: list[dict[str, Any]], day: str) -> dict[str, Any]:
    """The fixture document, stamped with how many stories fed it."""
    doc = document(edition_key, day)
    doc["selected_items"] = len(stories)
    return doc


async def _publish(doc: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Nothing is written: the store seam is replaced by its return contract."""
    return {
        "ok": True,
        "dry_run": dry_run,
        "slug": doc.get("slug"),
        "url": doc.get("url"),
        "revision": 0 if dry_run else 1,
        "title": doc.get("edition_title") or doc.get("title"),
    }


async def _deliver(
    edition_key: str, doc: dict[str, Any], published: dict[str, Any], dry_run: bool
) -> dict[str, Any]:
    """The real payload renderer, a fake transport: nothing leaves the box."""
    payload = delivery.telegram_payload(edition_key, doc, published)
    return {
        "ok": True,
        "dry_run": dry_run,
        "message_ids": [] if dry_run else [9001],
        "method": payload["method"],
        "payload": payload,
    }


def offline_deps(**overrides: Any) -> Deps:
    """Offline dependencies; any port may be replaced by keyword."""
    return replace(
        Deps(
            collect=_collect,
            score=_score,
            dedupe=adapters.dedupe_stories,
            route=adapters.route_story,
            write=_write,
            edit=adapters.edit_document,
            publish=_publish,
            deliver=_deliver,
        ),
        **overrides,
    )


__all__ = ["BODIES", "HEADINGS", "SOURCES", "STORIES", "document", "offline_deps"]
