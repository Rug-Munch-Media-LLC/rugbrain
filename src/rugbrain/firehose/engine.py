"""FirehoseEngine — the central continuous ingestion engine."""

import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx

from .models import FeedSource, FirehoseStats
from .pipeline import IngestionPipeline
from .sources import FeedSources, build_default_sources

logger = logging.getLogger("rag.firehose")

__all__ = ["FirehoseEngine", "get_firehose"]


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
        self._pipeline = IngestionPipeline()
        self._feeds = FeedSources(self._client)
        self._start_time = time.monotonic()
        assert self._feeds is not None
        self._sources = build_default_sources(self._feeds)

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
