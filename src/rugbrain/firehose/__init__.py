"""
RAG Firehose - Continuous Intelligence Ingestion Engine
========================================================

Self-feeding RAG pipeline that continuously pulls, filters, and ingests
crypto intelligence from multiple sources at different cadences.

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │                    FIREHOSE ENGINE                        │
  │                                                          │
  │  Hourly (news/social)  Daily (scams/wallets)  Weekly     │
  │       │                      │                    │       │
  │       ▼                      ▼                    ▼       │
  │  ┌─────────┐          ┌──────────┐          ┌──────────┐ │
  │  │ News RSS │          │ Etherscan│          │ FAISS    │ │
  │  │ CT Rundn │          │ Chainab. │          │ rebuild  │ │
  │  │ Social   │          │ Rekt DB  │          │ RAGAS    │ │
  │  │ Sentiment│          │ Solana   │          │ eval     │ │
  │  └────┬─────┘          └────┬─────┘          └────┬─────┘ │
  │       │                      │                     │      │
  │       └──────────────────────┼─────────────────────┘      │
  │                              ▼                            │
  │              ┌────────────────────────────┐               │
  │              │   SMART INGESTION PIPELINE  │               │
  │              │  Filter → Dedup → Extract   │               │
  │              │  → Classify → Embed → Store │               │
  │              └────────────┬───────────────┘               │
  │                           ▼                               │
  │              ┌────────────────────────────┐               │
  │              │       RAG COLLECTIONS       │               │
  │              │  known_scams, news_articles │               │
  │              │  forensic_reports, etc.     │               │
  │              └────────────┬───────────────┘               │
  │                           ▼                               │
  │              ┌────────────────────────────┐               │
  │              │      FEEDBACK LOOP          │               │
  │              │  Scanner hits → boost docs  │               │
  │              │  False positives → penalize │               │
  │              └────────────────────────────┘               │
  └─────────────────────────────────────────────────────────┘

This module serves as both the package initializer and the facade,
replacing the original monolithic firehose.py.
"""

import asyncio
import contextlib
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiofiles
import httpx

from .news import pull_news_rss as _pull_news_rss
from .news import pull_prediction_markets as _pull_prediction_markets
from .twitter import pull_ct_rundown as _pull_ct_rundown
from .twitter import pull_social_sentiment as _pull_social_sentiment

logger = logging.getLogger("rag.firehose")

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

RAG_API = "http://localhost:8000/api/v1/rag"

# Per-collection size caps (auto-evict oldest on overflow)
COLLECTION_CAPS = {
    "known_scams": 50000,
    "scam_patterns": 5000,
    "forensic_reports": 10000,
    "contract_audits": 5000,
    "wallet_profiles": 100000,
    "news_articles": 20000,
    "market_intel": 5000,
    "token_analysis": 50000,
    "transaction_patterns": 10000,
    "social_sentiment": 10000,
    "general": 10000,
}

# Quality thresholds (skip docs below this score)
MIN_QUALITY_SCORE = 30  # 0-100

# Rate limits (docs per minute per collection)
RATE_LIMITS = {
    "known_scams": 60,
    "news_articles": 30,
    "social_sentiment": 20,
    "default": 30,
}

# Content hash TTL in Redis (7 days for news, 30 for scams)
HASH_TTL = {
    "news_articles": 604800,  # 7 days
    "social_sentiment": 604800,  # 7 days
    "known_scams": 2592000,  # 30 days
    "default": 1209600,  # 14 days
}

# ──────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────


@dataclass
class FeedSource:
    """A data source the firehose pulls from."""

    name: str
    collection: str
    cadence_seconds: int
    fetch_fn: Any = field(repr=False)
    enabled: bool = True
    description: str = ""
    last_run: float = 0
    runs: int = 0
    docs_ingested: int = 0
    docs_skipped: int = 0
    errors: int = 0


@dataclass
class FirehoseStats:
    """Current firehose statistics."""

    running: bool = False
    uptime_seconds: float = 0
    total_sources: int = 0
    total_ingested: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_activity: float = 0


# ──────────────────────────────────────────────────────────────
# Smart Ingestion Pipeline
# ──────────────────────────────────────────────────────────────


class IngestionPipeline:
    """Filters, deduplicates, and enriches documents before RAG storage."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._rate_trackers: dict[str, list[float]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _check_rate(self, collection: str) -> bool:
        """Return True if we're under the rate limit for this collection."""
        limit = RATE_LIMITS.get(collection, RATE_LIMITS["default"])
        now = time.monotonic()
        times = self._rate_trackers.setdefault(collection, [])
        times[:] = [t for t in times if now - t < 60]
        return len(times) < limit

    def _record_ingest(self, collection: str) -> None:
        """Record an ingestion for rate limiting."""
        self._rate_trackers.setdefault(collection, []).append(time.monotonic())

    def make_content_hash(self, content: str) -> str:
        """Deterministic hash for dedup."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def is_duplicate(self, content_hash: str, collection: str) -> bool:
        """Check if content hash already exists in Redis dedup set."""
        try:
            resp = await self.client.get(
                f"{RAG_API}/dedup-check",
                params={"hash": content_hash, "collection": collection},
                timeout=5,
            )
            if resp.status_code == 200:
                return bool(resp.json().get("exists", False))
        except Exception:
            logger.warning("swallowed exception", exc_info=True)
        return False

    def score_quality(self, content: str, metadata: dict[str, Any]) -> int:
        """Score document quality 0-100. Skip low-signal content."""
        score = 50

        if len(content) > 500:
            score += 15
        elif len(content) > 200:
            score += 10
        elif len(content) < 50:
            score -= 20

        entity_count = 0
        if metadata.get("address"):
            entity_count += 1
        if metadata.get("chain"):
            entity_count += 1
        if metadata.get("token"):
            entity_count += 1
        if metadata.get("protocol"):
            entity_count += 1
        score += entity_count * 5

        high_authority = {"etherscan", "chainabuse", "rekt", "certik", "slowmist", "peckshield"}
        if metadata.get("source", "").lower() in high_authority:
            score += 20

        if metadata.get("severity") == "critical":
            score += 15
        elif metadata.get("severity") == "high":
            score += 10

        if metadata.get("is_variant"):
            score -= 10

        return max(0, min(100, score))

    def extract_entities(self, content: str) -> dict[str, Any]:
        """Extract addresses, chains, tokens from content."""
        import re

        entities: dict[str, Any] = {"addresses": [], "chains": [], "tokens": [], "protocols": []}

        evm_addrs = re.findall(r"0x[a-fA-F0-9]{40}", content)
        entities["addresses"].extend(evm_addrs[:10])

        sol_addrs = re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", content)
        entities["addresses"].extend([a for a in sol_addrs[:10] if a not in entities["addresses"]])

        known_chains = [
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "solana",
            "base",
            "fantom",
            "gnosis",
            "celo",
            "zksync",
            "linea",
            "scroll",
            "mantle",
            "sui",
            "aptos",
            "near",
            "tron",
            "bitcoin",
        ]
        for chain in known_chains:
            if chain.lower() in content.lower():
                entities["chains"].append(chain)

        tokens = re.findall(r"\$([A-Z]{2,10})", content)
        entities["tokens"].extend(tokens[:10])

        known_protocols = [
            "uniswap",
            "aave",
            "curve",
            "balancer",
            "sushi",
            "pancake",
            "raydium",
            "jupiter",
            "orca",
            "marinade",
            "lido",
            "eigenlayer",
            "compound",
            "maker",
            "yearn",
            "convex",
        ]
        for proto in known_protocols:
            if proto.lower() in content.lower():
                entities["protocols"].append(proto)

        return entities

    async def ingest(
        self, collection: str, content: str, metadata: dict[str, Any], doc_id: str | None = None
    ) -> dict[str, Any]:
        """Run the full pipeline: dedup → quality → extract → classify → store."""

        content_hash = self.make_content_hash(content)
        if await self.is_duplicate(content_hash, collection):
            return {"status": "duplicate", "hash": content_hash}

        quality = self.score_quality(content, metadata)
        if quality < MIN_QUALITY_SCORE:
            return {"status": "skipped", "reason": "low_quality", "score": quality}

        entities = self.extract_entities(content)
        metadata.update(
            {
                "entities": entities,
                "quality_score": quality,
                "content_hash": content_hash,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
        )

        if not self._check_rate(collection):
            return {"status": "rate_limited", "collection": collection}

        try:
            payload = {
                "collection": collection,
                "content": content,
                "metadata": metadata,
            }
            if doc_id:
                payload["doc_id"] = doc_id

            resp = await self.client.post(f"{RAG_API}/ingest", json=payload, timeout=30)
            self._record_ingest(collection)

            if resp.status_code == 200:
                result = resp.json()
                task = asyncio.create_task(self._mark_ingested(content_hash, collection))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
                return {
                    "status": "ingested",
                    "id": result.get("id"),
                    "quality": quality,
                    "entities": entities,
                }
            else:
                return {"status": "error", "code": resp.status_code, "detail": resp.text[:200]}
        except Exception as e:
            return {"status": "error", "detail": str(e)[:200]}

    async def _mark_ingested(self, content_hash: str, collection: str) -> None:
        """Mark content hash in Redis dedup set."""
        try:
            ttl = HASH_TTL.get(collection, HASH_TTL["default"])
            await self.client.post(
                f"{RAG_API}/dedup-mark",
                json={"hash": content_hash, "collection": collection, "ttl": ttl},
                timeout=5,
            )
        except Exception:
            logger.warning("swallowed exception", exc_info=True)

    async def ingest_batch(self, docs: list[dict[str, Any]]) -> dict[str, int]:
        """Ingest multiple documents with rate limiting."""
        stats = {"ingested": 0, "duplicate": 0, "skipped": 0, "errors": 0}

        for doc in docs:
            collection = doc.get("collection", "general")
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})
            doc_id = doc.get("doc_id")

            result = await self.ingest(collection, content, metadata, doc_id)
            status = result.get("status", "error")
            if status == "ingested":
                stats["ingested"] += 1
            elif status == "duplicate":
                stats["duplicate"] += 1
            elif status == "skipped":
                stats["skipped"] += 1
            else:
                stats["errors"] += 1

            await asyncio.sleep(0.05)

        return stats


# ──────────────────────────────────────────────────────────────
# Feed Sources - Orchestrator
# ──────────────────────────────────────────────────────────────


class FeedSources:
    """All data sources the firehose can pull from.

    Delegates to source-specific modules for twitter, news, etc.
    Chain-data sources (etherscan, solana, pattern extraction) remain
    inline here as they don't fit a dedicated source module.
    """

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    # ── Hourly: News (delegated) ──

    async def pull_news_rss(self) -> list[dict[str, Any]]:
        return await _pull_news_rss(self.client)

    async def pull_ct_rundown(self) -> list[dict[str, Any]]:
        return await _pull_ct_rundown(self.client)

    async def pull_social_sentiment(self) -> list[dict[str, Any]]:
        return await _pull_social_sentiment(self.client)

    async def pull_prediction_markets(self) -> list[dict[str, Any]]:
        return await _pull_prediction_markets(self.client)

    # ── Daily: Scam Databases (inline) ──

    async def pull_etherscan_labels(self) -> list[dict[str, Any]]:
        """Pull etherscan labeled addresses (via existing CSV)."""
        docs: list[dict[str, Any]] = []
        # __file__ is rugbrain/firehose/__init__.py -> go up 2 levels to rugbrain/../data/
        csv_rel = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "etherscan_phish_hack.csv"
        )
        csv_path = await asyncio.get_running_loop().run_in_executor(None, os.path.abspath, csv_rel)
        # Fall back to rugbrain/../data/ (original firehose.py location)
        if not await asyncio.get_running_loop().run_in_executor(None, os.path.exists, csv_path):
            csv_fallback = os.path.join(
                os.path.dirname(__file__), "..", "data", "etherscan_phish_hack.csv"
            )
            csv_path = await asyncio.get_running_loop().run_in_executor(
                None, os.path.abspath, csv_fallback
            )

        if not await asyncio.get_running_loop().run_in_executor(None, os.path.exists, csv_path):
            return docs

        import csv

        try:
            async with aiofiles.open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(
                    await asyncio.get_running_loop().run_in_executor(
                        None,
                        csv.DictReader,  # type: ignore[arg-type]  # csv.DictReader accepts any file-like
                        f,
                    )
                )

            for row in rows:
                addr = row.get("address", "").strip()
                if not addr:
                    continue
                content = (
                    f"Etherscan label: {addr} on {row.get('chain', 'Ethereum')}. "
                    f"Tag: {row.get('name_tag', '')}. Type: {row.get('label_type', 'scam')}."
                )
                docs.append(
                    {
                        "collection": "known_scams",
                        "content": content,
                        "metadata": {
                            "address": addr.lower(),
                            "chain": (row.get("chain", "Ethereum") or "Ethereum").lower(),
                            "label_type": row.get("label_type", "scam"),
                            "source": "etherscan",
                            "severity": "critical"
                            if "phish" in row.get("label_type", "").lower()
                            else "high",
                        },
                    }
                )
        except Exception as e:
            logger.warning("Etherscan pull failed: %s", e)

        return docs

    async def pull_solana_scams(self) -> list[dict[str, Any]]:
        """Pull Solana token registry flagged tokens."""
        docs: list[dict[str, Any]] = []
        try:
            resp = await self.client.get(
                "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json",
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                for token in data.get("tokens", []):
                    tags = [t.lower() for t in token.get("tags", [])]
                    if any(kw in str(tags) for kw in ["scam", "spam", "fake"]):
                        docs.append(
                            {
                                "collection": "known_scams",
                                "content": f"Solana scam token: {token.get('name', '')} "
                                f"({token.get('symbol', '')}) "
                                f"at {token.get('address', '')}. Tags: {tags}.",
                                "metadata": {
                                    "address": token.get("address", ""),
                                    "name": token.get("name", ""),
                                    "symbol": token.get("symbol", ""),
                                    "chain": "solana",
                                    "source": "solana_token_registry",
                                    "tags": tags,
                                    "severity": "high",
                                },
                            }
                        )
        except Exception as e:
            logger.warning("Solana pull failed: %s", e)

        return docs

    # ── Weekly: Pattern Extraction ──

    async def extract_scam_patterns(self) -> list[dict[str, Any]]:
        """Extract common patterns from confirmed scam documents."""
        docs: list[dict[str, Any]] = []
        try:
            resp = await self.client.get(
                f"{RAG_API}/search",
                params={
                    "q": "rug pull honeypot scam confirmed",
                    "collection": "known_scams",
                    "limit": 50,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if len(results) >= 10:
                    content_parts = []
                    for r in results[:20]:
                        c = r.get("content", "")[:200]
                        if c:
                            content_parts.append(c)

                    combined = "Common scam patterns observed: " + " | ".join(content_parts)
                    docs.append(
                        {
                            "collection": "scam_patterns",
                            "content": combined[:5000],
                            "metadata": {
                                "source": "pattern_extraction",
                                "pattern_count": len(results),
                                "extracted_at": datetime.now(UTC).isoformat(),
                            },
                        }
                    )
        except Exception as e:
            logger.warning("Pattern extraction failed: %s", e)

        return docs


# ──────────────────────────────────────────────────────────────
# Firehose Engine
# ──────────────────────────────────────────────────────────────


class FirehoseEngine:
    """The central continuous ingestion engine."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._client: httpx.AsyncClient | None = None
        self._pipeline: IngestionPipeline | None = None
        self._feeds: FeedSources | None = None
        self._sources: list[FeedSource] = []
        self._start_time: float = 0
        self.stats = FirehoseStats()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the firehose engine."""
        if self._running:
            logger.info("Firehose already running")
            return

        self._client = httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=20))
        self._pipeline = IngestionPipeline(self._client)
        self._feeds = FeedSources(self._client)
        self._start_time = time.monotonic()

        self._sources = [
            FeedSource(
                "ct_rundown",
                "news_articles",
                900,
                self._feeds.pull_ct_rundown,
                True,
                "CT Rundown stories",
            ),
            FeedSource(
                "social_sentiment",
                "social_sentiment",
                900,
                self._feeds.pull_social_sentiment,
                True,
                "Social sentiment and scam alerts",
            ),
            FeedSource(
                "news_rss",
                "news_articles",
                3600,
                self._feeds.pull_news_rss,
                True,
                "200+ RSS crypto news feeds",
            ),
            FeedSource(
                "prediction_markets",
                "market_intel",
                21600,
                self._feeds.pull_prediction_markets,
                True,
                "Polymarket predictions",
            ),
            FeedSource(
                "etherscan_labels",
                "known_scams",
                86400,
                self._feeds.pull_etherscan_labels,
                True,
                "Etherscan phish/hack labeled addresses",
            ),
            FeedSource(
                "solana_scams",
                "known_scams",
                86400,
                self._feeds.pull_solana_scams,
                True,
                "Solana token registry flagged tokens",
            ),
            FeedSource(
                "scam_patterns",
                "scam_patterns",
                259200,
                self._feeds.extract_scam_patterns,
                True,
                "Extract common patterns from confirmed scams",
            ),
        ]

        self.stats.total_sources = len(self._sources)
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Firehose started with %s sources", len(self._sources))

    async def stop(self) -> None:
        """Stop the firehose engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._client:
            await self._client.aclose()
        logger.info("Firehose stopped")

    async def _run_loop(self) -> None:
        """Main firehose loop - checks sources and runs those due."""
        logger.info("Firehose loop started")

        while self._running:
            now = time.monotonic()

            for source in self._sources:
                if not source.enabled:
                    continue
                if now - source.last_run < source.cadence_seconds:
                    continue

                source.last_run = now
                source.runs += 1
                self.stats.last_activity = now

                try:
                    logger.debug("Firehose: pulling %s", source.name)
                    docs = await source.fetch_fn()

                    if docs:
                        assert self._pipeline is not None  # set in start()
                        stats = await self._pipeline.ingest_batch(docs)
                        source.docs_ingested += stats["ingested"]
                        source.docs_skipped += stats["duplicate"] + stats["skipped"]
                        source.errors += stats["errors"]

                        self.stats.total_ingested += stats["ingested"]
                        self.stats.total_skipped += stats["duplicate"] + stats["skipped"]
                        self.stats.total_errors += stats["errors"]

                        logger.info(
                            "Firehose %s: +%s new, %s dup, %s skip, %s err (total: %s)",
                            source.name,
                            stats["ingested"],
                            stats["duplicate"],
                            stats["skipped"],
                            stats["errors"],
                            source.docs_ingested,
                        )
                except Exception as e:
                    logger.error("Firehose %s failed: %s", source.name, e)
                    source.errors += 1
                    self.stats.total_errors += 1

            async with self._lock:
                self.stats.sources = {
                    s.name: {
                        "collection": s.collection,
                        "cadence_min": s.cadence_seconds // 60,
                        "runs": s.runs,
                        "ingested": s.docs_ingested,
                        "skipped": s.docs_skipped,
                        "errors": s.errors,
                        "last_run_ago": int(now - s.last_run) if s.last_run else -1,
                    }
                    for s in self._sources
                }

            await asyncio.sleep(30)

    async def feed_now(self, source_name: str) -> dict[str, Any]:
        """Manually trigger a specific feed source immediately."""
        for source in self._sources:
            if source.name == source_name:
                try:
                    docs = await source.fetch_fn()
                    assert self._pipeline is not None  # set in start()
                    stats = await self._pipeline.ingest_batch(docs)
                    source.runs += 1
                    source.docs_ingested += stats["ingested"]
                    source.last_run = time.monotonic()
                    self.stats.total_ingested += stats["ingested"]
                    return {"source": source_name, "docs_fetched": len(docs), **stats}
                except Exception as e:
                    return {"source": source_name, "error": str(e)}
        return {"error": f"Source '{source_name}' not found"}

    def get_status(self) -> dict[str, Any]:
        """Get current firehose status."""
        return {
            "running": self._running,
            "uptime_seconds": int(time.monotonic() - self._start_time) if self._start_time else 0,
            "total_sources": self.stats.total_sources,
            "total_ingested": self.stats.total_ingested,
            "total_skipped": self.stats.total_skipped,
            "total_errors": self.stats.total_errors,
            "sources": self.stats.sources,
        }


# ──────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────

_firehose: FirehoseEngine | None = None


def get_firehose() -> FirehoseEngine:
    global _firehose
    if _firehose is None:
        _firehose = FirehoseEngine()
    return _firehose


__all__ = [
    "COLLECTION_CAPS",
    "HASH_TTL",
    "MIN_QUALITY_SCORE",
    "RAG_API",
    "RATE_LIMITS",
    "FeedSource",
    "FeedSources",
    "FirehoseEngine",
    "FirehoseStats",
    "IngestionPipeline",
    "get_firehose",
]
