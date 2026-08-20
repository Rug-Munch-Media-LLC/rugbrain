"""RugBrain social intelligence — crypto X, Reddit, and Farcaster.

Aggregates social signals without paid APIs:

  * **X/Twitter** — tracked crypto accounts via SearXNG (self-hosted,
    local port 8089), which mirrors x.com/nitter content. No X API key needed.
  * **Reddit** — ``r/cryptocurrency``, ``r/ethdev``, ``r/solana`` via the
    free Reddit JSON API (``.json`` endpoints, no auth; requires a UA).
  * **Farcaster** — casts from the fleet's own Hubble node
    (``degenfeed-hubble`` container, HTTP API on :2281 by default). The
    node is probed lazily and skipped when unreachable.

Every run extracts trending topics, a lexicon-based sentiment score, and
ranks influential posts by engagement (Reddit score / Farcaster reactions).
All posts are embedded and stored in the Qdrant ``social_intel`` collection,
plus one synthetic trend-summary document per run.

Usage::

    python -m rugbrain.social_intel --once
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.bge_embedders import InfinityBGEEmbedder
from rugbrain.ingest_common import CrawlDocument, doc_from_article, store_documents
from rugbrain.qdrant_store import QdrantVectorStore

log = logging.getLogger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8089").rstrip("/")
REDDIT_SUBREDDITS = tuple(
    os.getenv("SOCIAL_REDDIT_SUBREDDITS", "cryptocurrency,ethdev,solana").split(",")
)
HUBBLE_URL = os.getenv("HUBBLE_URL", "http://127.0.0.1:2281").rstrip("/")
HUBBLE_FIDS = tuple(os.getenv("HUBBLE_FIDS", "1,2,3").split(","))
SOCIAL_COLLECTION = os.getenv("CRAWLER_SOCIAL_COLLECTION", "social_intel")
HTTP_TIMEOUT = int(os.getenv("CRAWLER_HTTP_TIMEOUT", "30"))
USER_AGENT = "RMI-RAG/1.0 (+https://rugmunch.io; social-intel)"

POSITIVE_WORDS = (
    "bullish",
    "moon",
    "gain",
    "surge",
    "rally",
    "pump",
    "buy",
    "strong",
    "great",
    "win",
    "up",
    "soar",
    "breakout",
    "adopt",
    "partnership",
    "upgrade",
    "profit",
)
NEGATIVE_WORDS = (
    "bearish",
    "dump",
    "rug",
    "hack",
    "exploit",
    "crash",
    "scam",
    "panic",
    "sell",
    "drop",
    "vuln",
    "breach",
    "loss",
    "down",
    "liquidation",
    "fud",
    "bankrupt",
)
STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "with",
    "this",
    "that",
    "from",
    "are",
    "was",
    "were",
    "has",
    "have",
    "will",
    "its",
    "it's",
    "not",
    "you",
    "your",
    "our",
    "we",
    "they",
    "them",
    "their",
    "is",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "be",
    "as",
    "i",
    "can",
    "all",
    "what",
    "about",
    "how",
    "when",
    "more",
}


# ── Fetching ─────────────────────────────────────────────────────────────


async def fetch_x_posts(
    client: httpx.AsyncClient, accounts: tuple[str, ...]
) -> list[CrawlDocument]:
    """Crypto X/Twitter posts via SearXNG JSON results per tracked account."""
    docs: list[CrawlDocument] = []
    for handle in accounts:
        try:
            r = await client.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": f"site:x.com/{handle} OR site:nitter.net/{handle} crypto",
                    "format": "json",
                    "safesearch": "0",
                    "time_range": "day",
                },
            )
            if r.status_code != 200:
                continue
            for res in ((r.json() or {}).get("results") or [])[:5]:
                if not isinstance(res, dict):
                    continue
                title = str(res.get("title", "")).strip()
                if not title:
                    continue
                docs.append(
                    doc_from_article(
                        title=title[:300],
                        content=f"{title}. {res.get('content', '')}".strip()[:2000],
                        url=str(res.get("url", "")),
                        source=f"x:{handle}",
                        collection=SOCIAL_COLLECTION,
                        category="crypto_twitter",
                        metadata={"handle": handle, "engine": "searxng"},
                    )
                )
        except Exception as e:
            log.debug("social_x_failed handle=%s err=%s", handle, str(e)[:120])
    return docs


async def fetch_reddit_posts(
    client: httpx.AsyncClient, subreddits: tuple[str, ...], limit: int = 25
) -> list[CrawlDocument]:
    """Top/new posts from crypto subreddits via the free Reddit JSON API."""
    docs: list[CrawlDocument] = []
    for sub in subreddits:
        sub = sub.strip().lower()
        if not sub:
            continue
        try:
            r = await client.get(
                f"https://www.reddit.com/r/{sub}/new.json",
                params={"limit": limit},
                headers={"User-Agent": USER_AGENT},
            )
            if r.status_code != 200:
                log.debug("reddit_status sub=%s status=%d", sub, r.status_code)
                continue
            children = ((r.json() or {}).get("data") or {}).get("children") or []
            for child in children[:limit]:
                post = (child or {}).get("data") or {}
                title = str(post.get("title", "")).strip()
                if not title:
                    continue
                selftext = str(post.get("selftext", "")).strip()[:1500]
                docs.append(
                    doc_from_article(
                        title=title[:300],
                        content=f"{title}. {selftext}".strip()[:2000],
                        url="https://www.reddit.com" + str(post.get("permalink", "")),
                        source=f"reddit:{sub}",
                        collection=SOCIAL_COLLECTION,
                        category="reddit",
                        published=str(post.get("created_utc", "")),
                        metadata={
                            "subreddit": sub,
                            "score": int(post.get("score") or 0),
                            "num_comments": int(post.get("num_comments") or 0),
                            "author": str(post.get("author", "")),
                        },
                    )
                )
        except Exception as e:
            log.debug("reddit_failed sub=%s err=%s", sub, str(e)[:120])
    return docs


async def fetch_farcaster_casts(
    client: httpx.AsyncClient, fids: tuple[str, ...]
) -> list[CrawlDocument]:
    """Recent casts from the fleet Hubble node (degenfeed-hubble, :2281)."""
    docs: list[CrawlDocument] = []
    try:
        info = await client.get(f"{HUBBLE_URL}/v1/info", timeout=5.0)
        if info.status_code != 200:
            log.debug("hubble_unreachable status=%d", info.status_code)
            return docs
    except Exception as e:
        log.debug("hubble_unreachable err=%s", str(e)[:120])
        return docs

    messages: list[dict[str, Any]] = []
    # Newer hubs expose /v1/recent-casts; older ones only castsByFid.
    try:
        r = await client.get(f"{HUBBLE_URL}/v1/recent-casts", params={"limit": 25})
        if r.status_code == 200:
            messages = ((r.json() or {}).get("messages") or [])[:25]
    except Exception:
        messages = []
    if not messages:
        for fid in fids:
            try:
                r = await client.get(
                    f"{HUBBLE_URL}/v1/castsByFid", params={"fid": fid, "limit": 10}
                )
                if r.status_code == 200:
                    messages.extend(((r.json() or {}).get("messages") or [])[:10])
            except Exception as e:
                log.debug("hubble_fid_failed fid=%s err=%s", fid, str(e)[:120])
                continue

    seen: set[str] = set()
    for msg in messages:
        data = (msg or {}).get("data") or {}
        cast = (data.get("castBody") or {}) or {}
        text = str(cast.get("text", "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        timestamp = str(data.get("timestamp", ""))[:19]
        reactions = ((data.get("reactions") or {}) or {}).get("count", 0)
        docs.append(
            doc_from_article(
                title=text[:300],
                content=text[:2000],
                url="",
                source="farcaster",
                collection=SOCIAL_COLLECTION,
                category="farcaster",
                published=timestamp,
                metadata={
                    "fid": data.get("fid", ""),
                    "reactions": int(reactions or 0),
                    "hub": HUBBLE_URL,
                },
            )
        )
    return docs


# ── Analysis helpers ─────────────────────────────────────────────────────


def analyze_sentiment(docs: list[CrawlDocument]) -> float:
    """Lexicon sentiment score in [-1, 1]; 0 when no signal present."""
    words: list[str] = []
    for doc in docs:
        words.extend(re.findall(r"[a-z']+", (doc.title + " " + doc.content).lower()))
    if not words:
        return 0.0
    positive = sum(1 for w in words if w in POSITIVE_WORDS)
    negative = sum(1 for w in words if w in NEGATIVE_WORDS)
    total = positive + negative
    if total == 0:
        return 0.0
    return round((positive - negative) / total, 3)


def trending_topics(docs: list[CrawlDocument], limit: int = 10) -> list[tuple[str, int]]:
    """Most-mentioned tokens across documents (stopwords filtered)."""
    counter: Counter[str] = Counter()
    for doc in docs:
        tokens = [
            t
            for t in re.findall(r"[a-z0-9]+", (doc.title + " " + doc.content).lower())
            if len(t) > 3 and t not in STOPWORDS
        ]
        counter.update(set(tokens))
    return counter.most_common(limit)


def _engagement(doc: CrawlDocument) -> int:
    meta = doc.metadata
    score = int(meta.get("score") or 0)
    comments = int(meta.get("num_comments") or 0)
    reactions = int(meta.get("reactions") or 0)
    return score + comments + reactions


def influential_posts(docs: list[CrawlDocument], limit: int = 5) -> list[CrawlDocument]:
    """Rank posts by engagement heuristic (Reddit score, Farcaster reactions)."""
    ranked = sorted(docs, key=_engagement, reverse=True)
    return ranked[:limit]


# ── Orchestration ────────────────────────────────────────────────────────


async def collect_social_intel(
    dry_run: bool = False,
    limit: int = 50,
    accounts: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Gather X + Reddit + Farcaster, analyze, and store in Qdrant.

    Returns ``{"posts": n, "inserted": n, "failed": n, "sentiment": f,
    "trending": [...]}``. Skips a source gracefully when unavailable.
    """
    from rugbrain.web_crawler import X_ACCOUNTS as DEFAULT_X_ACCOUNTS

    accounts = accounts or DEFAULT_X_ACCOUNTS
    docs: list[CrawlDocument] = []
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}
    ) as client:
        x_docs, reddit_docs, fc_docs = await asyncio.gather(
            fetch_x_posts(client, accounts),
            fetch_reddit_posts(client, REDDIT_SUBREDDITS),
            fetch_farcaster_casts(client, HUBBLE_FIDS),
        )
        docs.extend(x_docs)
        docs.extend(reddit_docs)
        docs.extend(fc_docs)

    # Dedup by URL / content fingerprint.
    unique: list[CrawlDocument] = []
    seen: set[str] = set()
    for doc in docs:
        key = doc.url or doc.content[:120]
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)
    unique = unique[:limit]

    sentiment = analyze_sentiment(unique)
    trending = trending_topics(unique)
    top_posts = influential_posts(unique)

    # Synthetic trend summary document so Qdrant can answer "what's trending".
    summary = doc_from_article(
        title=f"Social intel digest {datetime.now(UTC).date().isoformat()}",
        content=(
            f"Trending crypto topics: {', '.join(f'{t} ({n})' for t, n in trending)}. "
            f"Social sentiment: {sentiment:+.3f} (positive > 0, negative < 0). "
            "Top posts: " + " | ".join(p.title for p in top_posts[:3])
        ),
        url="",
        source="social_intel_summary",
        collection=SOCIAL_COLLECTION,
        category="digest",
        published=datetime.now(UTC).isoformat(),
        metadata={
            "sentiment": sentiment,
            "trending": [{"topic": t, "count": n} for t, n in trending],
            "top_posts": [{"title": p.title, "source": p.source} for p in top_posts],
        },
    )
    unique.append(summary)

    if dry_run:
        log.info(
            "social_intel_dry_run posts=%d sentiment=%s trending=%s",
            len(unique),
            sentiment,
            trending,
        )
        return {
            "posts": len(unique),
            "inserted": 0,
            "failed": 0,
            "sentiment": sentiment,
            "trending": trending,
        }

    embedder = InfinityBGEEmbedder(
        infinity_url=os.getenv("INFINITY_URL", "http://172.22.0.1:8004/v1")
    )
    store = QdrantVectorStore()
    try:
        result = await store_documents(unique, embedder, store)
    finally:
        await store.close()
    log.info("social_intel_done posts=%d result=%s", len(unique), result)
    return {
        "posts": len(unique),
        "inserted": result["inserted"],
        "failed": result["failed"],
        "sentiment": sentiment,
        "trending": trending,
    }


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RugBrain social intelligence")
    parser.add_argument("--once", action="store_true", help="single collection pass")
    parser.add_argument("--dry-run", action="store_true", help="fetch only, don't store")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    async def run() -> None:
        result = await collect_social_intel(dry_run=args.dry_run, limit=args.limit)
        print(json.dumps(result, default=str))

    asyncio.run(run())


if __name__ == "__main__":
    _main()


__all__ = [
    "analyze_sentiment",
    "collect_social_intel",
    "fetch_farcaster_casts",
    "fetch_reddit_posts",
    "fetch_x_posts",
    "influential_posts",
    "trending_topics",
]
