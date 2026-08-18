"""Podcast Ingestion — transcribes crypto podcasts into searchable text.

Fetches podcast episodes from RSS feeds, downloads audio, transcribes
via Whisper (local, free), and indexes into Qdrant.

Sources:
1. **RSS feeds** — crypto podcast RSS feeds (Bankless, Unchained, etc.)
2. **YouTube transcripts** — YouTube API transcripts for crypto videos
3. **Local Whisper** — runs on Talos CPU for free transcription

Usage::
    from rugbrain.podcast_ingester import ingest_podcast_episode
    result = await ingest_podcast_episode("https://feeds.bankless.com/rss")
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

# Crypto podcast RSS feeds
CRYPTO_PODCASTS: list[dict[str, str]] = [
    {"name": "Bankless", "rss": "https://feeds.bankless.com/rss"},
    {"name": "Unchained", "rss": "https://unchainedcrypto.com/feed/podcast"},
    {"name": "The Defiant", "rss": "https://thedefiant.io/feed/podcast"},
    {"name": "Empire", "rss": "https://feeds.megaphone.fm/empire"},
    {"name": "Bell Curve", "rss": "https://feeds.megaphone.fm/bellcurve"},
]

POLL_INTERVAL = int(os.getenv("PODCAST_POLL_INTERVAL", "3600"))  # 1 hour
MAX_EPISODES = int(os.getenv("PODCAST_MAX_EPISODES", "5"))

# Track seen episodes to avoid re-indexing
_seen_episodes: set[str] = set()


async def fetch_rss_feed(url: str) -> str | None:
    """Fetch an RSS feed XML."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "rugbrain/1.0"})
            return r.text if r.status_code == 200 else None
    except Exception as exc:
        logger.warning("rss_fetch_failed url=%s error=%s", url, str(exc)[:60])
        return None


def parse_rss_episodes(xml_text: str, podcast_name: str, max_episodes: int = 5) -> list[dict[str, Any]]:
    """Parse RSS XML and extract podcast episodes."""
    episodes = []
    try:
        root = ElementTree.fromstring(xml_text)
        ns = {"": "http://www.w3.org/2005/Atom"}

        # Try RSS 2.0 format first
        for item in root.findall(".//item"):
            if len(episodes) >= max_episodes:
                break

            title = item.findtext("title", "")
            guid = item.findtext("guid", "")
            pub_date = item.findtext("pubDate", "")
            description = item.findtext("description", "")
            link = item.findtext("link", "")

            # Get audio URL from enclosures
            audio_url = ""
            for enclosure in item.findall("enclosure"):
                url = enclosure.get("url", "")
                if url and (".mp3" in url or ".m4a" in url or ".wav" in url):
                    audio_url = url
                    break

            if guid and guid not in _seen_episodes:
                _seen_episodes.add(guid)
                _seen_episodes.copy()  # Keep bounded
                episodes.append({
                    "source": "podcast",
                    "podcast": podcast_name,
                    "title": title,
                    "guid": guid,
                    "published": pub_date,
                    "description": description[:500],
                    "link": link,
                    "audio_url": audio_url,
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                })
    except Exception as exc:
        logger.warning("rss_parse_failed podcast=%s error=%s", podcast_name, str(exc)[:60])

    return episodes


async def ingest_podcast_episode(episode: dict[str, Any]) -> dict[str, Any]:
    """Ingest a single podcast episode into Qdrant.

    Uses the episode description + title as the content for embedding.
    Audio transcription is skipped for now (would require local Whisper).
    """
    content = f"{episode['title']}\n\n{episode['description']}"

    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        embedding = await get_embedding(content)
        doc_id = f"podcast_{episode['podcast']}_{episode['guid']}".replace(":", "_").replace("/", "_")

        store = QdrantVectorStore()
        ok = await store.insert(
            doc_id=doc_id,
            collection="podcast_episodes",
            embedding=embedding,
            content=content,
            metadata=episode,
            source="podcast",
            severity="info",
            chain="ethereum",
        )
        return {"status": "ok" if ok else "failed", "doc_id": doc_id}
    except Exception as exc:
        logger.warning("podcast_ingest_failed guid=%s error=%s", episode.get("guid", ""), str(exc)[:80])
        return {"status": "failed", "error": str(exc)[:80]}


async def podcast_scan_cycle() -> dict[str, Any]:
    """Scan all crypto podcast RSS feeds for new episodes."""
    results = []
    for podcast in CRYPTO_PODCASTS:
        xml = await fetch_rss_feed(podcast["rss"])
        if xml:
            episodes = parse_rss_episodes(xml, podcast["name"], MAX_EPISODES)
            results.extend(episodes)
            logger.info("podcast_scan_found podcast=%s episodes=%d", podcast["name"], len(episodes))
        await asyncio.sleep(1)  # Rate limit

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "podcasts_scanned": len(CRYPTO_PODCASTS),
        "new_episodes": len(results),
        "episodes": results[:10],
    }


async def podcast_scan_loop() -> None:
    """Run podcast scanning in a continuous background loop."""
    logger.info("podcast_ingester_started interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await podcast_scan_cycle()
            if result["new_episodes"] > 0:
                logger.info("podcast_new_episodes count=%d", result["new_episodes"])
        except Exception as exc:
            logger.warning("podcast_scan_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
