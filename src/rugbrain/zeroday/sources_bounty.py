"""Bounty / contest board adapters (public listing pages, no auth).

Only what the page states is emitted: program name + canonical URL. Reward and
scope stay ``None`` unless a board exposes them in machine-readable form —
inventing a payout number would poison the whole feed.

Hats is registered as disabled: ``app.hats.finance`` does not resolve from
Talos (verified 2026-10-25), so there is no honest live source to poll.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from rugbrain.zeroday.feeds import get_text
from rugbrain.zeroday.schema import Record, slugify

log = logging.getLogger(__name__)

IMMUNEFI_URL = "https://immunefi.com/bug-bounty/"
CODE4RENA_URL = "https://code4rena.com/contests"
CANTINA_URL = "https://cantina.xyz/competitions"
SHERLOCK_URL = "https://audits.sherlock.xyz/contests"
HACKENPROOF_URL = "https://hackenproof.com/programs"

_ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _anchors(page: str, prefix: str, base: str) -> list[tuple[str, str]]:
    """(absolute url, visible text) for anchors under *prefix*, deduped by url."""
    found: dict[str, str] = {}
    for href, inner in _ANCHOR_RE.findall(page):
        path = href.split("?", 1)[0].split("#", 1)[0]
        if not path.startswith(prefix) or len(path) <= len(prefix):
            continue
        slug = path[len(prefix) :].strip("/").split("/", 1)[0]
        if not slug or slug.startswith("page-"):
            continue
        text = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", inner))).strip()
        if not 2 <= len(text) <= 160:
            continue
        found.setdefault(f"{base}{prefix}{slug}/", text)
    return list(found.items())


def _listing(
    page: str, *, source: str, kind: str, prefix: str, base: str, limit: int
) -> list[Record]:
    records = [
        Record(
            source=source,
            kind=kind,
            title=title,
            url=url,
            confidence="confirmed",
            citations=(url,),
        )
        for url, title in _anchors(page, prefix, base)
    ]
    log.info("%s records=%d", source, len(records))
    return records[:limit]


async def immunefi(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Immunefi bug-bounty programs (server-rendered listing)."""
    page = await get_text(client, IMMUNEFI_URL)
    return _listing(
        page or "",
        source="immunefi",
        kind="bounty",
        prefix="/bug-bounty/",
        base="https://immunefi.com",
        limit=limit,
    )


async def code4rena(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Code4rena audit contests."""
    page = await get_text(client, CODE4RENA_URL)
    return _listing(
        page or "",
        source="code4rena",
        kind="contest",
        prefix="/audits/",
        base="https://code4rena.com",
        limit=limit,
    )


async def cantina(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Cantina competitions (only server-rendered rows are visible)."""
    page = await get_text(client, CANTINA_URL)
    return _listing(
        page or "",
        source="cantina",
        kind="contest",
        prefix="/competitions/",
        base="https://cantina.xyz",
        limit=limit,
    )


async def sherlock(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Sherlock audit contests."""
    page = await get_text(client, SHERLOCK_URL)
    return _listing(
        page or "",
        source="sherlock",
        kind="contest",
        prefix="/contests/",
        base="https://audits.sherlock.xyz",
        limit=limit,
    )


async def hackenproof(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """HackenProof programs."""
    page = await get_text(client, HACKENPROOF_URL)
    return _listing(
        page or "",
        source="hackenproof",
        kind="bounty",
        prefix="/programs/",
        base="https://hackenproof.com",
        limit=limit,
    )


def program_key(record: Record) -> str:
    """Stable identity for a program listing (used by ``bounty_scope``)."""
    return f"program:{slugify(record.url.rstrip('/').rsplit('/', 1)[-1])}"


__all__ = [
    "CANTINA_URL",
    "CODE4RENA_URL",
    "HACKENPROOF_URL",
    "IMMUNEFI_URL",
    "SHERLOCK_URL",
    "cantina",
    "code4rena",
    "hackenproof",
    "immunefi",
    "program_key",
    "sherlock",
]
