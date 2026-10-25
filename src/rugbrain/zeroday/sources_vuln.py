"""Advisory adapters: GitHub Security Advisories, NVD and CISA KEV.

All three are public, keyless endpoints (OSV lives in ``sources_osv``). Advisories and CVEs enter as kind
``0day`` — a disclosed, not-yet-weaponised vulnerability — while PoC and
incident feeds live in ``sources_flow``.

Shape notes (verified against live responses 2026-10-25):
  * ``api.github.com/advisories`` → list of advisory objects.
  * NVD 2.0                       → ``{"vulnerabilities": [{"cve": {...}}]}``.
  * CISA KEV                      → ``{"vulnerabilities": [...]}`` (1.8 MB).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.zeroday.feeds import get_json, within_days
from rugbrain.zeroday.schema import Record, iso_utc, severity_from_cvss, severity_from_text

log = logging.getLogger(__name__)

GH_ADVISORIES_URL = "https://api.github.com/advisories?sort=published&direction=desc&per_page={n}"
NVD_URL = (
    "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage={n}&startIndex=0&noRejected"
)
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
KEV_SOURCE_URL = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"


def _first_cvss(advisory: dict[str, Any]) -> str:
    cvss = advisory.get("cvss") or {}
    return severity_from_text(cvss.get("score") if isinstance(cvss, dict) else None)


def _ghsa(advisory: dict[str, Any]) -> Record | None:
    ghsa = str(advisory.get("ghsa_id") or "").strip()
    if not ghsa:
        return None
    references = advisory.get("references") or []
    urls = tuple(str(r.get("url")) for r in references if isinstance(r, dict) and r.get("url"))[:5]
    versions: list[str] = []
    fixed: str | None = None
    for vuln in advisory.get("vulnerabilities") or []:
        if not isinstance(vuln, dict):
            continue
        rng = str(vuln.get("vulnerable_version_range") or "").strip()
        if rng:
            versions.append(rng)
        patched = vuln.get("first_patched_version")
        if isinstance(patched, dict) and patched.get("identifier"):
            fixed = str(patched["identifier"])
    poc = next((u for u in urls if "poc" in u.lower() or "exploit" in u.lower()), None)
    external = [ghsa]
    if advisory.get("cve_id"):
        external.append(str(advisory["cve_id"]))
    return Record(
        source="github_advisories",
        kind="0day",
        title=str(advisory.get("summary") or ghsa),
        url=str(advisory.get("html_url") or f"https://github.com/advisories/{ghsa}"),
        published_at=iso_utc(advisory.get("published_at")),
        severity=_first_cvss(advisory)
        if advisory.get("cvss")
        else severity_from_text(advisory.get("severity")),
        affected_versions=tuple(versions[:5]),
        fixed_version=fixed,
        poc_url=poc,
        confidence="confirmed",
        citations=urls,
        external_ids=tuple(external),
    )


async def github_advisories(client: httpx.AsyncClient, limit: int = 60) -> list[Record]:
    """Recent GitHub Security Advisories."""
    data = await get_json(client, GH_ADVISORIES_URL.format(n=max(1, min(limit, 100))))
    if not isinstance(data, list):
        return []
    records = [r for item in data if isinstance(item, dict) and (r := _ghsa(item))]
    log.info("github_advisories records=%d", len(records))
    return records[:limit]


def _nvd(item: dict[str, Any]) -> Record | None:
    cve = item.get("cve") if isinstance(item, dict) else None
    if not isinstance(cve, dict) or not cve.get("id"):
        return None
    cve_id = str(cve["id"])
    description = next(
        (
            str(d.get("value"))
            for d in cve.get("descriptions") or []
            if isinstance(d, dict) and d.get("lang") == "en"
        ),
        cve_id,
    )
    score: Any = None
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        metrics = cve.get("metrics", {}).get(key) or []
        if metrics and isinstance(metrics[0], dict):
            score = (metrics[0].get("cvssData") or {}).get("baseScore")
            break
    refs = tuple(
        str(r.get("url"))
        for r in cve.get("references") or []
        if isinstance(r, dict) and r.get("url")
    )[:5]
    return Record(
        source="nvd",
        kind="0day",
        title=description[:200],
        url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
        published_at=iso_utc(cve.get("published")),
        severity=severity_from_cvss(score) if score is not None else "unknown",
        confidence="confirmed",
        citations=refs,
        external_ids=(cve_id,),
    )


async def nvd_recent(client: httpx.AsyncClient, limit: int = 40) -> list[Record]:
    """Most recently published CVEs (NVD 2.0, keyless rate limit 5/30s)."""
    data = await get_json(client, NVD_URL.format(n=max(1, min(limit, 100))))
    if not isinstance(data, dict):
        return []
    records = [
        r
        for item in data.get("vulnerabilities") or []
        if isinstance(item, dict) and (r := _nvd(item))
    ]
    log.info("nvd_recent records=%d", len(records))
    return records[:limit]


def _kev(vuln: dict[str, Any]) -> Record | None:
    cve_id = str(vuln.get("cveID") or "").strip()
    if not cve_id:
        return None
    ransomware = bool(vuln.get("knownRansomwareCampaignUse"))
    return Record(
        source="cisa_kev",
        kind="0day",
        title=f"{cve_id} — {vuln.get('vulnerabilityName') or vuln.get('product') or 'KEV'}",
        url=KEV_SOURCE_URL,
        published_at=iso_utc(vuln.get("dateAdded")),
        # KEV membership is the fact; "high" reflects known exploitation, not a CVSS score.
        severity="high" if not ransomware else "critical",
        protocol=str(vuln.get("vendorProject") or "") or None,
        confidence="confirmed",
        citations=(KEV_SOURCE_URL,),
        external_ids=(cve_id,),
    )


async def cisa_kev(client: httpx.AsyncClient, limit: int = 200, days: int = 45) -> list[Record]:
    """KEV entries added in the last *days* (full catalog is 1.8 MB, so windowed)."""
    data = await get_json(client, KEV_URL)
    if not isinstance(data, dict):
        return []
    records: list[Record] = []
    for vuln in data.get("vulnerabilities") or []:
        if not isinstance(vuln, dict) or not within_days(vuln.get("dateAdded"), days):
            continue
        record = _kev(vuln)
        if record is not None:
            records.append(record)
    log.info("cisa_kev records=%d window=%dd", len(records), days)
    return records[:limit]


__all__ = [
    "cisa_kev",
    "github_advisories",
    "nvd_recent",
]
