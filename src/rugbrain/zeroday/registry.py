"""The source catalogue: what we poll, how it is classified, what is off.

``enabled=False`` entries are kept visible with the reason they are off — a
disabled source that silently disappears is how feeds rot unnoticed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from rugbrain.zeroday import sources_bounty as bounty
from rugbrain.zeroday import sources_flow as flow
from rugbrain.zeroday import sources_osv as osv
from rugbrain.zeroday import sources_vuln as vuln
from rugbrain.zeroday.feeds import make_client
from rugbrain.zeroday.schema import Record, iso_utc

log = logging.getLogger(__name__)

Fetcher = Callable[[httpx.AsyncClient, int], Awaitable[list[Record]]]


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """One pollable public source."""

    name: str
    kind: str
    fetch: Fetcher
    trust: str
    enabled: bool = True
    note: str = ""


@dataclass(frozen=True, slots=True)
class SourceResult:
    """Outcome of one source poll (errors are data, not exceptions)."""

    name: str
    kind: str
    ok: bool
    records: list[Record] = field(default_factory=list)
    error: str | None = None
    elapsed_ms: int = 0
    fetched_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.name,
            "kind": self.kind,
            "ok": self.ok,
            "records": len(self.records),
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "fetched_at": self.fetched_at,
        }


async def _blocksec(client: httpx.AsyncClient, limit: int) -> list[Record]:
    return await flow.medium_feed(
        client, "blocksec_medium", flow.MEDIUM_FEEDS["blocksec_medium"], limit
    )


async def _peckshield(client: httpx.AsyncClient, limit: int) -> list[Record]:
    return await flow.medium_feed(
        client, "peckshield_medium", flow.MEDIUM_FEEDS["peckshield_medium"], limit
    )


async def _immunefi_medium(client: httpx.AsyncClient, limit: int) -> list[Record]:
    return await flow.medium_feed(
        client, "immunefi_medium", flow.MEDIUM_FEEDS["immunefi_medium"], limit
    )


SPECS: tuple[SourceSpec, ...] = (
    SourceSpec("github_advisories", "0day", vuln.github_advisories, "primary"),
    SourceSpec("osv", "0day", osv.osv_watchlist, "primary"),
    SourceSpec("nvd", "0day", vuln.nvd_recent, "primary"),
    SourceSpec("cisa_kev", "0day", vuln.cisa_kev, "primary"),
    SourceSpec("exploit_db", "exploit", flow.exploitdb, "primary"),
    SourceSpec("packetstorm", "exploit", flow.packetstorm, "primary"),
    SourceSpec("metasploit", "exploit", flow.metasploit, "primary"),
    SourceSpec("defillama_hacks", "exploit", flow.defillama_hacks, "primary"),
    SourceSpec("blocksec_medium", "exploit", _blocksec, "verified"),
    SourceSpec("peckshield_medium", "exploit", _peckshield, "verified"),
    SourceSpec("immunefi_medium", "exploit", _immunefi_medium, "verified"),
    SourceSpec("immunefi", "bounty", bounty.immunefi, "primary"),
    SourceSpec("hackenproof", "bounty", bounty.hackenproof, "primary"),
    SourceSpec("code4rena", "contest", bounty.code4rena, "primary"),
    SourceSpec("cantina", "contest", bounty.cantina, "primary"),
    SourceSpec("sherlock", "contest", bounty.sherlock, "primary"),
    SourceSpec(
        "rekt",
        "exploit",
        flow.exploitdb,
        "secondary",
        enabled=False,
        note="handled by ingest_sources.rekt (leaderboard scrape); RSS is dead (HTTP 500)",
    ),
    SourceSpec(
        "defihacklabs",
        "exploit",
        flow.exploitdb,
        "secondary",
        enabled=False,
        note="handled by ingest_sources.defihacklabs corpus source (PoC repo, not a feed)",
    ),
    SourceSpec(
        "slowmist",
        "exploit",
        flow.exploitdb,
        "secondary",
        enabled=False,
        note="no verified public feed; X-first reporting (verified 2026-10-25)",
    ),
    SourceSpec(
        "cyvers",
        "exploit",
        flow.exploitdb,
        "secondary",
        enabled=False,
        note="medium feed returns an empty body (verified 2026-10-25)",
    ),
    SourceSpec(
        "hats",
        "bounty",
        bounty.hackenproof,
        "secondary",
        enabled=False,
        note="app.hats.finance does not resolve from Talos (verified 2026-10-25)",
    ),
)


def active() -> tuple[SourceSpec, ...]:
    """Enabled sources only."""
    return tuple(spec for spec in SPECS if spec.enabled)


async def fetch_source(
    client: httpx.AsyncClient, spec: SourceSpec, limit: int = 40
) -> SourceResult:
    """Poll one source; any failure becomes ``ok=False`` plus the error text."""
    started = time.perf_counter()
    fetched_at = iso_utc(datetime.now(UTC).timestamp()) or ""
    try:
        records = await spec.fetch(client, limit)
        return SourceResult(
            name=spec.name,
            kind=spec.kind,
            ok=True,
            records=records,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            fetched_at=fetched_at,
        )
    except Exception as exc:
        log.warning("zeroday_source_failed name=%s err=%s", spec.name, str(exc)[:200])
        return SourceResult(
            name=spec.name,
            kind=spec.kind,
            ok=False,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            fetched_at=fetched_at,
        )


async def fetch_all(
    limit: int = 40,
    only: list[str] | None = None,
    concurrency: int = 6,
) -> list[SourceResult]:
    """Poll every enabled source with bounded concurrency, sharing one client."""
    specs = [spec for spec in active() if only is None or spec.name in set(only)]
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with make_client() as client:

        async def run(spec: SourceSpec) -> SourceResult:
            async with semaphore:
                return await fetch_source(client, spec, limit)

        return list(await asyncio.gather(*(run(spec) for spec in specs)))


__all__ = ["SPECS", "Fetcher", "SourceResult", "SourceSpec", "active", "fetch_all", "fetch_source"]
