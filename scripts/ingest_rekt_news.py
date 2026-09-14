#!/usr/bin/env python3
"""Ingest Rekt News historical hacks into the Qdrant ``hacks_v2`` collection.

Source: Rekt News public leaderboard (https://rekt.news/leaderboard)

The leaderboard page embeds a ``__NEXT_DATA__`` JSON blob with 300+ entries.
Each entry contains: title (protocol), slug, hack date, amount lost (USD),
tags (chains / attack vectors), and an excerpt describing the incident.

For each hack we:
  1. Build a structured description: protocol, date, amount, attack vector,
     chain, excerpt.
  2. Embed with qwen3-embedding-4b (2560-dim dense) via (OpenAI-compatible /embeddings).
  3. Upsert into the ``hacks_v2`` collection (the migrated hybrid
     collection; the legacy ``hacks`` name was migrated away and no alias
     exists, so writing to it would create an empty fragment).

Fallback: if the Rekt leaderboard cannot be fetched, the script falls back
to the DeFiHackLabs data (which covers most major exploits) stored in
``threat_intel_v2`` — it synthesizes hack entries from those points.

Idempotent: point IDs are deterministic seeds derived from the hack slug +
content hash which the store converts to UUIDv5, so re-running overwrites
the same points.

Usage:
    python scripts/ingest_rekt_news.py --limit 20
    python scripts/ingest_rekt_news.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://127.0.0.1:8004/v1) with qwen3-embedding-4b loaded
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_rekt")

REKT_LEADERBOARD_URL = "https://rekt.news/leaderboard"
COLLECTION = "hacks_v2"
EMBEDDING_DIM = 2560
SOURCE = "rekt_news"
REQUEST_TIMEOUT = 30

# Attack vector keywords → canonical category (for metadata/tags).
VECTOR_KEYWORDS: list[tuple[str, str]] = [
    (r"reentrancy", "reentrancy"),
    (r"flash ?loan", "flash-loan"),
    (r"oracle", "oracle-manipulation"),
    (r"price ?manipulation", "oracle-manipulation"),
    (r"access ?control", "access-control"),
    (r"bridge", "bridge-exploit"),
    (r"governance", "governance-attack"),
    (r"signature", "signature-malleability"),
    (r"withdraw", "withdrawal-exploit"),
    (r"overflow|underflow", "integer-overflow"),
    (r"private ?key", "key-compromise"),
    (r"rug", "rug-pull"),
    (r"exploit", "smart-contract-exploit"),
]


def _chunk_text(text: str, size: int = 1500, overlap: int = 100) -> list[str]:
    """Split long excerpts into windows (excerpts are usually short)."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _detect_vector(text: str) -> str:
    """Map free-text tags/excerpt to a canonical attack vector."""
    for pattern, vector in VECTOR_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return vector
    return "unknown"


def _parse_amount(amount: Any) -> float:
    """Normalize amount (int USD) to a float."""
    try:
        return float(amount)
    except (TypeError, ValueError):
        return 0.0


def _format_amount(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.2f}K"
    return f"${amount:,.0f}"


def _parse_date(raw: str) -> str:
    """Best-effort parse of M/D/YYYY strings to ISO date."""
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw or "")
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).date().isoformat()
        except ValueError:
            return raw
    return raw or ""


async def fetch_leaderboard() -> list[dict[str, Any]]:
    """Fetch the Rekt News leaderboard JSON via the __NEXT_DATA__ blob."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            REKT_LEADERBOARD_URL, headers={"User-Agent": "RMI-RAG/1.0 (data ingestion)"}
        )
        resp.raise_for_status()
        html = resp.text
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise RuntimeError("leaderboard page did not contain __NEXT_DATA__")
    data = json.loads(m.group(1))
    leaderboard: list[dict[str, Any]] = (
        data.get("props", {}).get("pageProps", {}).get("leaderboard", [])
    )
    logger.info("rekt_leaderboard_entries=%d", len(leaderboard))
    return leaderboard


async def fetch_article_body(slug: str) -> str:
    """Fetch a Rekt News article page and extract readable text (fail-open).

    Rekt articles embed their content in the same ``__NEXT_DATA__`` blob as
    the leaderboard; we parse ``pageProps.post.content`` or fall back to a
    lightweight HTML→text extraction.
    """
    if not slug:
        return ""
    url = f"https://rekt.news/{slug}/"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "RMI-RAG/1.0 (data ingestion)"})
            resp.raise_for_status()
            html = resp.text
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if m:
            data = json.loads(m.group(1))
            post = data.get("props", {}).get("pageProps", {}).get("post", {}) or {}
            content = post.get("content", "") or post.get("body", "") or ""
            if content:
                return re.sub(r"<[^>]+>", " ", content)
        # Fallback: strip scripts/styles and tags.
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:12000]
    except Exception as e:
        logger.debug("rekt_article_fetch_failed slug=%s err=%s", slug, str(e)[:120])
        return ""


def leaderboard_to_docs(entries: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """Transform leaderboard entries into structured hack docs."""
    docs: list[dict[str, Any]] = []
    for entry in entries:
        slug = (entry.get("slug") or "").strip()
        title = (entry.get("title") or "").strip()
        excerpt = (entry.get("excerpt") or "").strip()
        rekt = entry.get("rekt") or {}
        amount = _parse_amount(rekt.get("amount"))
        hack_date = _parse_date(rekt.get("date", ""))
        published = _parse_date(entry.get("date", ""))
        tags = entry.get("tags") or []
        if not title and not excerpt:
            continue
        vector = _detect_vector(f"{title} {' '.join(tags)} {excerpt}")
        url = f"https://rekt.news/{slug}/" if slug else REKT_LEADERBOARD_URL
        text = (
            f"{title}. ${_format_amount(amount)} lost on {hack_date or 'unknown date'}. "
            f"Attack vector: {vector}. Chain/tags: {', '.join(tags) if tags else 'unknown'}. "
            f"{excerpt}"
        )
        docs.append(
            {
                "slug": slug or _content_hash(title),
                "title": title,
                "amount_usd": amount,
                "amount_str": _format_amount(amount),
                "hack_date": hack_date,
                "published": published,
                "vector": vector,
                "tags": tags,
                "url": url,
                "text": text,
            }
        )
    if limit:
        docs = docs[:limit]
    return docs


async def ingest_hacks(docs: list[dict[str, Any]], dry_run: bool) -> int:
    """Embed + upsert hack docs into the ``hacks_v2`` collection."""
    if dry_run:
        logger.info("[DRY RUN] would ingest %d hacks into %s", len(docs), COLLECTION)
        return len(docs)

    # Provider-aware: DeepInfra when keyed (bulk lane), local fallback.
    # Do NOT pin the local endpoint — `_url=` was an invalid kwarg that
    # crashed this script, and pinned URLs force bulk onto the local server
    # (both fixed 2026-09-14).
    embedder = Qwen3Embedder()
    store = QdrantVectorStore()
    inserted = 0
    failed = 0
    try:
        for i, doc in enumerate(docs, start=1):
            for ci, chunk in enumerate(_chunk_text(doc["text"])):
                doc_id = f"rekt:{doc['slug']}:{_content_hash(chunk)}"
                metadata = {
                    "protocol": doc["title"],
                    "slug": doc["slug"],
                    "amount_usd": doc["amount_usd"],
                    "amount_str": doc["amount_str"],
                    "hack_date": doc["hack_date"],
                    "published": doc["published"],
                    "attack_vector": doc["vector"],
                    "tags": doc["tags"],
                    "source": SOURCE,
                    "source_url": doc["url"],
                    "chunk_index": ci,
                }
                try:
                    embedding = await embedder.embed_one(chunk[:8000])
                except Exception as e:
                    logger.warning("embed_failed slug=%s err=%s", doc["slug"], str(e)[:120])
                    failed += 1
                    continue
                if len(embedding) != EMBEDDING_DIM:
                    logger.warning("bad_dim slug=%s dim=%d", doc["slug"], len(embedding))
                    failed += 1
                    continue
                ok = await store.insert(
                    doc_id=doc_id,
                    collection=COLLECTION,
                    embedding=embedding,
                    content=chunk,
                    metadata=metadata,
                    source=SOURCE,
                    severity="high",
                    chain="multi",
                )
                if ok:
                    inserted += 1
                else:
                    failed += 1
            if i % 25 == 0 or i == len(docs):
                logger.info("progress %d/%d hacks, %d points upserted", i, len(docs), inserted)
    finally:
        await store.close()
    logger.info("done hacks=%d inserted=%d failed=%d", len(docs), inserted, failed)
    return inserted


async def fallback_from_defihacklabs(limit: int | None) -> list[dict[str, Any]]:
    """Fallback: synthesize hack docs from threat_intel_v2 DeFiHackLabs points.

    Used when the Rekt leaderboard cannot be fetched. Reads existing
    ``threat_intel_v2`` points with source=defihacklabs and builds one doc per
    unique protocol.
    """
    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
    docs: list[dict[str, Any]] = []
    offset: str | None = None
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            while True:
                body: dict[str, Any] = {
                    "limit": 1000,
                    "with_payload": True,
                    "with_vectors": False,
                    "filter": {
                        "must": [
                            {"key": "source", "match": {"value": "defihacklabs"}},
                            {"key": "collection", "match": {"value": "threat_intel_v2"}},
                        ]
                    },
                }
                if offset:
                    body["offset"] = offset
                r = await client.post(
                    f"{qdrant_url}/collections/threat_intel_v2/points/scroll", json=body
                )
                if r.status_code != 200:
                    logger.warning(
                        "defihacklabs_fallback_scroll_failed status=%d body=%s",
                        r.status_code,
                        r.text[:200],
                    )
                    break
                result = r.json().get("result", {})
                for point in result.get("points", []):
                    payload = point.get("payload") or {}
                    metadata = payload.get("metadata") or {}
                    content = payload.get("content", "") or ""
                    # `source` is written at payload top-level by the store
                    # (not inside metadata), so check it there.
                    if payload.get("source") != "defihacklabs" or not content:
                        continue
                    protocol = (
                        metadata.get("protocol") or metadata.get("contract_name") or "unknown"
                    )
                    amount = metadata.get("amount_lost", "")
                    vector = metadata.get("attack_type") or metadata.get("category", "unknown")
                    attack_date = metadata.get("attack_date", "")
                    slug = _content_hash(protocol + content[:80])
                    docs.append(
                        {
                            "slug": f"defihacklabs-{slug}",
                            "title": protocol,
                            "amount_usd": 0.0,
                            "amount_str": amount if amount else "unknown",
                            "hack_date": attack_date,
                            "published": "",
                            "vector": str(vector),
                            "tags": [str(vector), "defihacklabs"],
                            "url": metadata.get("source_url", ""),
                            "text": (
                                f"DeFi hack: {protocol}. Attack vector: {vector}. "
                                f"Amount: {amount}. Date: {attack_date or 'unknown'}. {content[:800]}"
                            ),
                        }
                    )
                nxt = result.get("next_page_offset")
                if not nxt:
                    break
                offset = nxt
    except Exception as e:
        logger.warning("defihacklabs_fallback_failed err=%s", str(e)[:150])
        return []

    # Deduplicate by slug.
    seen: dict[str, dict[str, Any]] = {}
    for d in docs:
        seen.setdefault(d["slug"], d)
    docs = list(seen.values())
    if limit:
        docs = docs[:limit]
    logger.info("fallback_defihacklabs_docs=%d", len(docs))
    return docs


async def main_async(args: argparse.Namespace) -> int:
    docs: list[dict[str, Any]] = []
    try:
        entries = await fetch_leaderboard()
        docs = leaderboard_to_docs(entries, args.limit)
        logger.info("rekt_leaderboard_ok docs=%d", len(docs))
        # Enhanced mode: fetch each article's full body and append it to the
        # excerpt doc so retrieval surfaces the actual analysis, not just the
        # one-line leaderboard summary. Fail-open on any per-article error.
        if args.articles and docs:
            enriched: list[dict[str, Any]] = []
            for doc in docs:
                slug = doc.get("slug", "")
                body = await fetch_article_body(slug)
                if body:
                    enriched.append({**doc, "text": f"{doc['text']}\n\nArticle body:\n{body}"})
                else:
                    enriched.append(doc)
            docs = enriched
            logger.info("rekt_articles_enriched=%d/%d", len(enriched), len(docs))
    except Exception as e:
        logger.warning(
            "rekt_leaderboard_fetch_failed err=%s — falling back to DeFiHackLabs", str(e)[:150]
        )
        docs = await fallback_from_defihacklabs(args.limit)

    if not docs:
        logger.error("no hack data available (leaderboard and fallback both failed)")
        return 0

    return await ingest_hacks(docs, args.dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Rekt News historical hacks into the hacks_v2 collection"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max hacks to ingest")
    parser.add_argument(
        "--articles",
        action="store_true",
        help="Also fetch each article's full body text (slower, richer embeddings)",
    )
    args = parser.parse_args()

    inserted = asyncio.run(main_async(args))
    print("=" * 60)
    print(f"REKT NEWS INGEST COMPLETE: {inserted} points in {COLLECTION}")
    print("=" * 60)


if __name__ == "__main__":
    main()
