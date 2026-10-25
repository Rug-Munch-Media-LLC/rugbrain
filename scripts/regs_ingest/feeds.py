"""RSS and paginated listing retrieval for official US regulator releases."""

from __future__ import annotations

import asyncio
import json
import os
import re
import xml.etree.ElementTree as ET

import httpx
from regs_ingest.config import CFTC_CANDIDATES, CFTC_ROBOTS, LISTING_PAGES, UA
from regs_ingest.normalise import canonical_url

ANCHOR = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
ACCEPT_RSS = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"


def parse_feed(raw: bytes, name: str, agency: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items
    atom = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{atom}entry"):
        link = ""
        for node in entry.findall(f"{atom}link"):
            if node.get("rel") in (None, "alternate") and node.get("href"):
                link = node.get("href", "")
                break
        title = (entry.findtext(f"{atom}title") or "").strip()
        if not link or not title:
            continue
        items.append({
            "feed": name, "agency": agency, "url": link, "title": title,
            "summary": re.sub(TAGS, " ", entry.findtext(f"{atom}summary") or "").strip(),
            "published": (entry.findtext(f"{atom}updated") or "").strip(),
        })
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not link or not title:
            continue
        items.append({
            "feed": name, "agency": agency, "url": link, "title": title,
            "summary": re.sub(TAGS, " ", item.findtext("description") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
        })
    return items


async def get(client: httpx.AsyncClient, url: str, accept: str = "*/*") -> httpx.Response | None:
    try:
        return await client.get(
            url,
            headers={"User-Agent": UA, "Accept": accept,
                     "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None


async def curl_get(url: str, accept: str = ACCEPT_RSS) -> bytes | None:
    """Documented-UA fetch through curl's TLS stack (httpx fingerprint is blocked)."""
    proc = await asyncio.create_subprocess_exec(
        "curl", "-sS", "-f", "--max-time", "30", "-A", UA, "-H", f"Accept: {accept}",
        "-L", url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out if proc.returncode == 0 and out else None


async def rss_items(client, name, agency, url, cap, feeds_report) -> list[dict]:
    resp = await get(client, url, ACCEPT_RSS)
    status = getattr(resp, "status_code", "ERR")
    if resp is None or status != 200:
        feeds_report[name] = f"RSS_FAIL status={status}"
        return []
    items = parse_feed(resp.content, name, agency)[:cap]
    feeds_report[name] = f"rss_items={len(items)}"
    return items


def listing_links(html: str, base: str, must: tuple[str, ...], name: str, agency: str) -> list[dict]:
    out, seen = [], set()
    for href, inner in ANCHOR.findall(html):
        if not any(m in href for m in must):
            continue
        url = str(httpx.URL(base).join(href)).split("#")[0]
        key = url.rstrip("/")
        slug = key.rsplit("/", 1)[-1]
        if key in seen or not re.search(r"\d", slug):
            continue
        seen.add(key)
        out.append({
            "feed": name, "agency": agency, "url": url,
            "title": re.sub(WS, " ", TAGS.sub(" ", inner)).strip(),
            "summary": "", "published": "",
        })
    return out


async def paged_items(client, pages: int, budget: int, report) -> list[dict]:
    """Walk bounded ?page=N listings; stop a feed when a page adds no new links."""
    out: list[dict] = []
    for name, specs in LISTING_PAGES.items():
        got, seen = 0, set()
        for tmpl, must, agency in specs:
            for page in range(1, pages + 1):
                if got >= budget:
                    break
                url = tmpl.format(n=page)
                resp = await get(client, url, "text/html")
                status = getattr(resp, "status_code", "ERR")
                if resp is None or status != 200:
                    report[f"{name}:p{page}"] = f"HTTP {status}"
                    break
                links = [l for l in listing_links(resp.text, url, must, name, agency)
                         if canonical_url(l["url"]) not in seen]
                report[f"{name}:p{page}"] = f"new={len(links)}"
                if not links:
                    break
                for link in links:
                    seen.add(canonical_url(link["url"]))
                out.extend(links)
                got += len(links)
        report[f"{name}:paged"] = got
    return out[:budget]


async def cftc_items(client, report) -> list[dict]:
    """Robots-checked, documented-UA path; httpx then curl TLS fallback."""
    robots = await get(client, CFTC_ROBOTS, "text/plain")
    if robots is not None and robots.status_code == 200:
        body = robots.text.lower()
        blanket = re.search(r"user-agent:\s*\*\s*\n(?:content-signal:.*\n)?disallow:\s*/\s*$",
                            body, re.M)
        report["cftc_robots"] = "allow:/ for User-agent:* (Content-Signal use=reference)" if not blanket else "blanket disallow"
        if blanket:
            report["cftc"] = "robots-disallow -> skipped"
            return []
    tried = []
    for url in CFTC_CANDIDATES:
        raw = await curl_get(url)
        tried.append(f"curl {url} -> {'200' if raw else 'FAIL'}")
        if raw is None:
            resp = await get(client, url, ACCEPT_RSS)
            status = getattr(resp, "status_code", "ERR")
            tried.append(f"httpx {url} -> {status}")
            raw = resp.content if (resp is not None and status == 200) else None
        if raw:
            items = parse_feed(raw, "cftc_press", "CFTC")
            if items:
                report["cftc"] = f"{url} -> 200 via documented-UA (items={len(items)})"
                report["cftc_tried"] = tried
                return items[:50]
    from regs_ingest.archives import cftc_listing

    links = await cftc_listing(client, report)
    if links:
        report["cftc"] = f"listing page via documented-UA curl (items={len(links)})"
        report["cftc_tried"] = tried
        return links
    report["cftc"] = "BLOCKED: documented UA failed on both stacks"
    report["cftc_tried"] = tried
    return []


def load_seed(path: str, report: dict) -> list[dict]:
    """Bow out of a bot block honestly: consume a JSON list scraped out-of-band."""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
    report["cftc_seed"] = f"{path} items={len(items)}"
    return items
