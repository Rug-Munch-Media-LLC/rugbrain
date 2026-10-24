"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Telegram teaser and OpenGraph metadata for a Daily Rug brief.

Separate from section rendering because these two outputs carry hard platform
limits: 4,096 characters for Telegram, and the fixed OpenGraph key set the
site's link cards read.
"""

from __future__ import annotations

import re
from typing import Any

BULLET_CHARS = 150
TELEGRAM_TARGET = 900


def _sentence(text: str) -> str:
    flat = " ".join(text.split())
    first = re.split(r"(?<=[.!?])\s+", flat, maxsplit=1)[0]
    return first if first.endswith((".", "!", "?")) else first + "."


def _bullet(body: str) -> str:
    """Drop the attribution prefix so a teaser bullet reads as a statement."""
    return re.sub(r"^According to [^,]+, ", "", _sentence(body))


def telegram_summary(doc: dict[str, Any], style: dict[str, Any]) -> str:
    """Telegram teaser: title, dek, three sourced bullets, canonical link."""
    hard_limit = int(style["length_budgets"]["telegram_hard_limit"])
    bullets = [
        f"- {_bullet(section['body'])[:BULLET_CHARS]}" for section in doc["sections"][:3]
    ]
    head = [doc["title"], "", doc["dek"], ""]
    tail = [
        "",
        "Read the full brief: " + doc["url"],
        f"{style['cta']['label']}: {style['cta']['url']}",
    ]
    text = "\n".join(head + bullets + tail)
    while len(text) > min(hard_limit, TELEGRAM_TARGET) and len(bullets) > 1:
        bullets.pop()
        text = "\n".join(head + bullets + tail)
    return text


def open_graph(doc: dict[str, Any], style: dict[str, Any]) -> dict[str, str]:
    """OpenGraph plus article metadata for the canonical story URL."""
    og = style["open_graph"]
    return {
        "og:title": doc["title"],
        "og:description": doc["dek"],
        "og:type": og["type"],
        "og:url": doc["url"],
        "og:site_name": og["site_name"],
        "og:image": og["image"],
        "og:image:alt": og["image_alt"],
        "article:published_time": f"{doc['date']}T00:00:00Z",
        "article:section": "Daily Rug",
    }
