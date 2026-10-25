"""Year-archive and listing-page fallbacks for regulator press releases."""

from __future__ import annotations

import re

from regs_ingest.feeds import curl_get, get, listing_links
from regs_ingest.normalise import canonical_url

FED_YEARS = (2026, 2025, 2024, 2023)
FED_TMPL = "https://www.federalreserve.gov/newsevents/pressreleases/{year}-press.htm"
FED_SLUG = re.compile(r"^[a-z]+[0-9]{8}[a-z]?\.htm$")


async def archive_items(client, budget: int, report: dict) -> list[dict]:
    """Fed year archives (the real pagination; ?page= repeats page 1)."""
    out: list[dict] = []
    seen: set[str] = set()
    for year in FED_YEARS:
        if len(out) >= budget:
            break
        url = FED_TMPL.format(year=year)
        resp = await get(client, url, "text/html")
        status = getattr(resp, "status_code", "ERR")
        if resp is None or status != 200:
            report[f"fed_archive:{year}"] = f"HTTP {status}"
            continue
        links = [
            link for link in listing_links(resp.text, url, ("/newsevents/pressreleases/",),
                                           "fed_press", "FederalReserve")
            if FED_SLUG.match(link["url"].rsplit("/", 1)[-1])
            and canonical_url(link["url"]) not in seen
        ]
        report[f"fed_archive:{year}"] = len(links)
        for link in links:
            seen.add(canonical_url(link["url"]))
        out.extend(links)
    report["fed_archive_total"] = len(out)
    return out[:budget]


async def cftc_listing(client, report: dict) -> list[dict]:
    """Documented-UA listing scrape (RSS body is not RSS 2.0 / is guarded)."""
    url = "https://www.cftc.gov/PressRoom/PressReleases"
    raw = await curl_get(url, "text/html")
    if not raw:
        report["cftc_listing"] = "curl FAIL"
        return []
    links = listing_links(raw.decode("utf-8", "replace"), url,
                          ("/PressRoom/PressReleases/",), "cftc_press", "CFTC")
    report["cftc_listing"] = f"links={len(links)}"
    return links[:50]
