"""OSV.dev adapter — package advisories for a bounded crypto watchlist.

OSV has no keyless "list everything" endpoint; ``/v1/query`` is per package,
so the watchlist is explicit and capped at ten packages per sweep.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from rugbrain.zeroday.feeds import post_json
from rugbrain.zeroday.schema import Record, iso_utc, severity_from_cvss

log = logging.getLogger(__name__)

OSV_QUERY_URL = "https://api.osv.dev/v1/query"

#: Crypto-adjacent packages whose advisories matter to our stack.
DEFAULT_OSV_PACKAGES = (
    "web3",
    "ethers",
    "viem",
    "wagmi",
    "@solana/web3.js",
    "solders",
    "eth-account",
    "bitcoinjs-lib",
    "alloy",
)

_CVSS_VALUE_RE = re.compile(r"(\d+\.\d+)")
_ECOSYSTEMS = ("npm", "PyPI", "Go", "crates.io", "Maven", "NuGet", "RubyGems", "Packagist")


def _osv(vuln: dict[str, Any]) -> Record | None:
    vuln_id = str(vuln.get("id") or "").strip()
    if not vuln_id:
        return None
    score = "unknown"
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict) and entry.get("score"):
            found = _CVSS_VALUE_RE.search(str(entry["score"]))
            if found:
                score = severity_from_cvss(found.group(1))
                break
    versions: list[str] = []
    fixed: str | None = None
    for affected in vuln.get("affected") or []:
        if not isinstance(affected, dict):
            continue
        versions.extend(str(v) for v in (affected.get("versions") or [])[:3])
        for rng in affected.get("ranges") or []:
            for event in (rng or {}).get("events") or []:
                if isinstance(event, dict) and event.get("fixed"):
                    fixed = str(event["fixed"])
    refs = tuple(
        str(r.get("url"))
        for r in (vuln.get("references") or [])
        if isinstance(r, dict) and r.get("url")
    )[:5]
    return Record(
        source="osv",
        kind="0day",
        title=str(vuln.get("summary") or vuln_id),
        url=f"https://osv.dev/vulnerability/{vuln_id}",
        published_at=iso_utc(vuln.get("published")),
        severity=score,
        affected_versions=tuple(versions[:5]),
        fixed_version=fixed,
        confidence="confirmed",
        citations=refs,
        external_ids=tuple([vuln_id] + [str(a) for a in (vuln.get("aliases") or [])]),
    )


async def osv_watchlist(
    client: httpx.AsyncClient,
    limit: int = 40,
    packages: tuple[str, ...] = DEFAULT_OSV_PACKAGES,
) -> list[Record]:
    """OSV advisories for the watchlist (first matching ecosystem per package)."""
    records: list[Record] = []
    for package in packages[:10]:
        for ecosystem in _ECOSYSTEMS:
            data = await post_json(
                client, OSV_QUERY_URL, {"package": {"name": package, "ecosystem": ecosystem}}
            )
            vulns = (data or {}).get("vulns") if isinstance(data, dict) else None
            if vulns:
                records.extend(r for v in vulns if isinstance(v, dict) and (r := _osv(v)))
                break
    log.info("osv_watchlist records=%d packages=%d", len(records), min(len(packages), 10))
    return records[:limit]


__all__ = ["DEFAULT_OSV_PACKAGES", "OSV_QUERY_URL", "osv_watchlist"]
