"""Exploit / incident adapters: PoC feeds, Metasploit modules, hack ledgers,
and security-team write-ups.

Facts come from the source; severity is only mapped from a stated score or
labelled band. DeFi loss amounts are stated by DeFiLlama and carried verbatim.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.zeroday.feeds import get_json, get_text, parse_feed, within_days
from rugbrain.zeroday.schema import Record, iso_utc

log = logging.getLogger(__name__)

EXPLOITDB_RSS = "https://www.exploit-db.com/rss.xml"
PACKETSTORM_RSS = "https://packetstormsecurity.com/feeds/pocs.xml"
METASPLOIT_COMMITS = (
    "https://api.github.com/repos/rapid7/metasploit-framework/commits"
    "?path=modules/exploits&per_page={n}"
)
DEFILLAMA_HACKS = "https://api.llama.fi/hacks"

#: Security-team Medium feeds (verified 200 + real bodies on 2026-10-25).
MEDIUM_FEEDS: dict[str, str] = {
    "blocksec_medium": "https://medium.com/feed/@BlockSecTeam",
    "peckshield_medium": "https://medium.com/feed/@PeckShield",
    "immunefi_medium": "https://medium.com/feed/immunefi",
}


def _feed_records(
    items: list[dict[str, str]], source: str, *, severity: str = "unknown"
) -> list[Record]:
    """RSS items → exploit records (the source states the PoC, not a score)."""
    records: list[Record] = []
    for item in items:
        records.append(
            Record(
                source=source,
                kind="exploit",
                title=item["title"][:250],
                url=item["link"],
                published_at=iso_utc(item.get("published")),
                severity=severity,
                confidence="confirmed" if source == "exploit_db" else "likely",
                citations=(item["link"],),
            )
        )
    return records


async def exploitdb(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Exploit-DB public PoC RSS."""
    text = await get_text(client, EXPLOITDB_RSS)
    records = _feed_records(parse_feed(text or ""), "exploit_db") if text else []
    log.info("exploitdb records=%d", len(records))
    return records[:limit]


async def packetstorm(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Packet Storm PoC feed (301s to the canonical host; client follows)."""
    text = await get_text(client, PACKETSTORM_RSS)
    records = _feed_records(parse_feed(text or ""), "packetstorm") if text else []
    log.info("packetstorm records=%d", len(records))
    return records[:limit]


def _commit(item: dict[str, Any]) -> Record | None:
    commit = item.get("commit") if isinstance(item, dict) else None
    if not isinstance(commit, dict):
        return None
    message = str(commit.get("message") or "").splitlines()[0].strip()
    url = str(item.get("html_url") or "")
    if not message or not url:
        return None
    return Record(
        source="metasploit",
        kind="exploit",
        title=message[:250],
        url=url,
        published_at=iso_utc((commit.get("author") or {}).get("date")),
        confidence="confirmed",
        external_ids=(str(item.get("sha") or "")[:12],),
        citations=(url,),
    )


async def metasploit(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Newest commits touching ``modules/exploits`` (module == working exploit)."""
    data = await get_json(client, METASPLOIT_COMMITS.format(n=max(1, min(limit, 100))))
    if not isinstance(data, list):
        return []
    records = [r for item in data if isinstance(item, dict) and (r := _commit(item))]
    log.info("metasploit records=%d", len(records))
    return records[:limit]


def _loss_severity(amount: Any) -> str:
    """Deterministic band from the stated USD loss (derived, never invented)."""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 10_000_000:
        return "critical"
    if value >= 1_000_000:
        return "high"
    if value >= 100_000:
        return "medium"
    return "low"


def _hack(item: dict[str, Any]) -> Record | None:
    name = str(item.get("name") or "").strip()
    if not name or not item.get("date"):
        return None
    chains = item.get("chain") or []
    amount = item.get("amount")
    return Record(
        source="defillama_hacks",
        kind="exploit",
        title=f"{name} — {item.get('technique') or item.get('classification') or 'exploit'}",
        url=f"https://defillama.com/hacks/{item.get('defillamaId') or ''}".rstrip("/"),
        published_at=iso_utc(item.get("date")),
        severity=_loss_severity(amount),
        chain=", ".join(str(c) for c in chains) if isinstance(chains, list) else None,
        protocol=name,
        loss_usd=float(amount) if isinstance(amount, (int, float)) else None,
        confidence="confirmed",
        citations=("https://api.llama.fi/hacks",),
    )


async def defillama_hacks(
    client: httpx.AsyncClient, limit: int = 40, days: int = 30
) -> list[Record]:
    """DeFiLlama hack ledger — protocol, chain, stated loss, technique."""
    data = await get_json(client, DEFILLAMA_HACKS)
    if not isinstance(data, list):
        return []
    records = [
        r
        for item in data
        if isinstance(item, dict) and within_days(item.get("date"), days) and (r := _hack(item))
    ]
    records.sort(key=lambda r: r.published_at or "", reverse=True)
    log.info("defillama_hacks records=%d window=%dd", len(records), days)
    return records[:limit]


async def medium_feed(
    client: httpx.AsyncClient, source: str, url: str, limit: int = 20
) -> list[Record]:
    """One security-team Medium feed (BlockSec / PeckShield / Immunefi)."""
    text = await get_text(client, url)
    if not text:
        return []
    records = _feed_records(parse_feed(text), source)
    log.info("%s records=%d", source, len(records))
    return records[:limit]


__all__ = [
    "DEFILLAMA_HACKS",
    "EXPLOITDB_RSS",
    "MEDIUM_FEEDS",
    "PACKETSTORM_RSS",
    "defillama_hacks",
    "exploitdb",
    "medium_feed",
    "metasploit",
    "packetstorm",
]
