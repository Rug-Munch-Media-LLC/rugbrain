"""The exploit monitor orchestrator: schedules pollers, runs the pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import (
    CRITICAL_NATS_SUBJECT,
    MONITOR_TICK,
    ONCHAIN_POLL_SECONDS,
    RPC_POLL_SECONDS,
    TWITTER_POLL_SECONDS,
)
from .models import CriticalAlert

log = logging.getLogger("rugbrain.realtime_alerts")


class ExploitMonitor:
    """Schedules the three monitors and runs the alert pipeline."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._stats: dict[str, Any] = {
            "started_at": "",
            "last_twitter_poll": "",
            "last_onchain_poll": "",
            "last_rpc_poll": "",
            "alerts_fired": 0,
            "alerts_deduped": 0,
            "errors": 0,
            "pipeline": {"nats": 0, "clickhouse": 0, "telegram": 0, "qdrant": 0, "webhooks": 0},
        }
        self._last_poll: dict[str, float] = {"twitter": 0.0, "onchain": 0.0, "rpc": 0.0}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    async def _handle_candidate(self, alert: CriticalAlert) -> None:
        from rugbrain import realtime_alerts as _pkg

        if await _pkg._store.record(alert):
            self._stats["alerts_fired"] += 1
        else:
            self._stats["alerts_deduped"] += 1
            return
        log.warning(
            "CRITICAL_ALERT id=%s source=%s chain=%s title=%s amount_usd=%s",
            alert.id,
            alert.source,
            alert.chain,
            alert.title,
            alert.amount_usd,
        )
        await self._pipeline(alert)

    async def _pipeline(self, alert: CriticalAlert) -> None:
        from rugbrain import realtime_alerts as _pkg

        # 1. NATS (Redis pub/sub fallback) — reuse AlertBus with the critical subject.
        try:
            bus = _pkg.AlertBus(subject=CRITICAL_NATS_SUBJECT)
            if await bus.connect():
                ok = await bus.publish_payload(alert.model_dump())
                if ok:
                    self._stats["pipeline"]["nats"] += 1
            await bus.close()
        except Exception as e:
            log.warning("pipeline_nats_failed: %s", e)

        # 2. ClickHouse critical_alerts ledger.
        try:
            if _pkg._critical_logger.log(alert):
                self._stats["pipeline"]["clickhouse"] += 1
        except Exception as e:
            log.warning("pipeline_clickhouse_failed: %s", e)

        # 3. Telegram.
        try:
            if await _pkg._send_telegram(alert):
                self._stats["pipeline"]["telegram"] += 1
        except Exception as e:
            log.warning("pipeline_telegram_failed: %s", e)

        # 4. Qdrant live_threats.
        try:
            if await _pkg.store_qdrant_live_threat(alert):
                self._stats["pipeline"]["qdrant"] += 1
        except Exception as e:
            log.warning("pipeline_qdrant_failed: %s", e)

        # 5. Community webhooks.
        try:
            result = await _pkg.deliver_webhooks(alert, client=self.client)
            self._stats["pipeline"]["webhooks"] += result["delivered"]
        except Exception as e:
            log.warning("pipeline_webhooks_failed: %s", e)

    async def _poll_source(self, name: str) -> None:
        from rugbrain import realtime_alerts as _pkg

        now = time.time()
        interval = {
            "twitter": TWITTER_POLL_SECONDS,
            "onchain": ONCHAIN_POLL_SECONDS,
            "rpc": RPC_POLL_SECONDS,
        }[name]
        if now - self._last_poll[name] < interval:
            return
        self._last_poll[name] = now
        candidates: list[CriticalAlert] = []
        try:
            if name == "twitter":
                candidates = await _pkg.poll_searxng_exploits(self.client)
                self._stats["last_twitter_poll"] = datetime.now(UTC).isoformat()
            elif name == "onchain":
                candidates = await _pkg.poll_onchain_anomalies()
                self._stats["last_onchain_poll"] = datetime.now(UTC).isoformat()
            elif name == "rpc":
                candidates = await _pkg.poll_rpc_large_txs(self.client)
                self._stats["last_rpc_poll"] = datetime.now(UTC).isoformat()
        except Exception as e:
            self._stats["errors"] += 1
            log.warning("monitor_poll_error source=%s err=%s", name, str(e)[:160])
            return
        for candidate in candidates:
            await self._handle_candidate(candidate)

    async def run_forever(self) -> None:
        self._stats["started_at"] = datetime.now(UTC).isoformat()
        log.info(
            "realtime_exploit_monitor: starting (twitter=%ss onchain=%ss rpc=%ss)",
            TWITTER_POLL_SECONDS,
            ONCHAIN_POLL_SECONDS,
            RPC_POLL_SECONDS,
        )
        while True:
            for name in ("twitter", "onchain", "rpc"):
                await self._poll_source(name)
            await asyncio.sleep(MONITOR_TICK)

    def stats(self) -> dict[str, Any]:
        return dict(self._stats)


_monitor = ExploitMonitor()
_monitor_task: asyncio.Task[Any] | None = None


async def start_realtime_monitor() -> bool:
    """Start the background exploit monitor (idempotent)."""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return True
    _monitor_task = asyncio.create_task(_monitor.run_forever(), name="realtime-exploit-monitor")
    log.info("realtime_exploit_monitor: task created")
    return True


async def stop_realtime_monitor() -> None:
    """Cancel the background exploit monitor and close its HTTP client."""
    global _monitor_task
    task = _monitor_task
    _monitor_task = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    await _monitor.close()


def is_realtime_monitor_running() -> bool:
    """True when the background exploit monitor task is alive."""
    return _monitor_task is not None and not _monitor_task.done()


def realtime_monitor_stats() -> dict[str, Any]:
    """Current exploit-monitor counters."""
    return _monitor.stats()
