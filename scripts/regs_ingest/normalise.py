"""Body extraction, canonical keys, and dedupe for regulator documents."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import httpx

from rugbrain.ingest_common import CrawlDocument
from regs_ingest.config import COLLECTION, LICENCE, UA

WS = re.compile(r"\s+")
NONWORD = re.compile(r"[^0-9a-zA-Z]+")


def canonical_url(url: str) -> str:
    u = httpx.URL(url)
    return f"{u.scheme or 'https'}://{u.host.lower()}{(u.path.rstrip('/') or '/')}"


def norm_text(text: str) -> str:
    return WS.sub(" ", NONWORD.sub(" ", text.lower())).strip()


def text_key(text: str) -> str:
    return hashlib.sha256(norm_text(text).encode("utf-8")).hexdigest()[:32]


async def fetch_page(client, item: dict, sem) -> dict:
    async with sem:
        try:
            resp = await client.get(item["url"], headers={"User-Agent": UA}, follow_redirects=True)
            if resp.status_code != 200:
                item["fetch_status"] = f"HTTP {resp.status_code}"
                return item
            html = resp.text
        except httpx.HTTPError as exc:
            item["fetch_status"] = type(exc).__name__
            return item
    body = ""
    try:
        import trafilatura

        body = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    except Exception:  # extraction is best-effort; fall back to tag-strip
        pass
    if len(body) < 200:
        text = re.sub(r"(?is)<(script|style|nav|header|footer).*?</\1>", " ", html)
        body = WS.sub(" ", re.sub(r"<[^>]+>", " ", text)).strip()
    item["body"] = body
    if not item.get("title"):
        item["title"] = (body[:120] or item["url"]).strip()
    return item


def dedupe_urls(cands: list[dict], existing_urls: set[str]) -> tuple[list[dict], int]:
    new, seen, dupes = [], set(existing_urls), 0
    for it in cands:
        key = canonical_url(it["url"])
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        new.append(it)
    return new, dupes


def dedupe_text(items: list[dict]) -> tuple[list[dict], int]:
    new, seen, dupes = [], set(), 0
    for it in items:
        key = text_key(it.get("body") or it.get("summary") or it["url"])
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        new.append(it)
    return new, dupes


def to_documents(items: list[dict]) -> list[CrawlDocument]:
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    docs = []
    for it in items:
        body = (it.get("body") or it.get("summary") or "").strip()
        if len(body) < 200:
            continue
        content = f"{it['title']}\n\n{body}"
        docs.append(CrawlDocument(
            title=it["title"],
            content=content,
            url=it["url"],
            source=f"{it['agency'].lower()}:{it['feed']}",
            collection=COLLECTION,
            category="regulation_enforcement",
            published=it.get("published", ""),
            severity="low",
            chain="none",
            metadata={
                "agency": it["agency"], "feed": it["feed"], "licence": LICENCE,
                "fetched_at": fetched_at, "doc_type": "official_press_release",
                "jurisdiction": "US", "primary_source": True,
                "doc_hash": text_key(content), "canonical_url": canonical_url(it["url"]),
            },
        ))
    return docs
