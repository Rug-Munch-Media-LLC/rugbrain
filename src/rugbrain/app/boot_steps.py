"""The boot table — every background subsystem, as data.

One job: declare what gets started, in what order, and how it is stopped.
``lifespan`` iterates :data:`BOOT_STEPS`; this module holds no control flow.

Ordering is load-bearing and mirrors the original ``lifespan`` exactly:

1. five MANAGED steps (explicit start/stop + ``is_running()`` + ``stats()``),
2. the first six BARE steps,
3. :func:`schema_step` — the canonical-collection validation, which ran in the
   middle of the bare loop,
4. the remaining BARE steps.

``STEP_NAMES`` is the assertion that keeps this honest: it must equal
``main.BOOT_STEP_NAMES`` from the pre-split implementation.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rugbrain.app import boot_runner, probes
from rugbrain.app.health_state import STATE

#: Subsystems with an explicit start/stop handle.
ManagedSpec = tuple[str, str, str, str, str, str, tuple[str, str] | None]

STOP_ORDER: tuple[str, ...] = (
    "fear_index_poller",
    "scanner_subscriber",
    "scanner_poller",
    "realtime_monitor",
    "news_story_updater",
)


@dataclass(frozen=True)
class BootStep:
    """One background subsystem. ``run`` is an async factory returning success."""

    name: str
    run: Callable[[], Awaitable[bool]]
    stop: Callable[[], Awaitable[None]] | None = None


MANAGED: tuple[ManagedSpec, ...] = (
    (
        "scanner_subscriber",
        "rugbrain.scanner_subscriber",
        "start_subscriber",
        "is_subscriber_running",
        "scanner_subscriber",
        "stop_subscriber",
        ("subscriber_stats", "subscriber_stats"),
    ),
    (
        "scanner_poller",
        "rugbrain.scanner_poller",
        "start_poller",
        "is_poller_running",
        "scanner_poller",
        "stop_poller",
        ("poller_stats", "poller_stats"),
    ),
    (
        "fear_index_poller",
        "rugbrain.fear_index",
        "start_fear_index_poller",
        "is_fear_index_poller_running",
        "fear_index_poller",
        "stop_fear_index_poller",
        None,
    ),
    (
        "realtime_monitor",
        "rugbrain.realtime_alerts",
        "start_realtime_monitor",
        "is_realtime_monitor_running",
        "realtime_monitor",
        "stop_realtime_monitor",
        ("realtime_monitor_stats", "realtime_monitor_stats"),
    ),
    (
        "news_story_updater",
        "rugbrain.news_realtime",
        "start_news_story_updater",
        "is_news_story_updater_running",
        "news_story_updater",
        "stop_news_story_updater",
        ("news_story_updater_stats", "news_story_updater_stats"),
    ),
)

#: Bare scan loops, split either side of the schema validation.
BARE_BEFORE: tuple[tuple[str, str, str], ...] = (
    ("mempool_scanner", "rugbrain.mempool_scanner", "mempool_scan_loop"),
    ("mev_detector", "rugbrain.mev_detector", "mev_scan_loop"),
    ("governance_aggregator", "rugbrain.governance_aggregator", "governance_scan_loop"),
    ("podcast_ingester", "rugbrain.podcast_ingester", "podcast_scan_loop"),
    ("smart_money_tracker", "rugbrain.smart_money_tracker", "smart_money_scan_loop"),
    ("youtube_ingester", "rugbrain.youtube_ingester", "youtube_scan_loop"),
    ("newsletter_ingester", "rugbrain.newsletter_ingester", "newsletter_scan_loop"),
)

BARE_AFTER: tuple[tuple[str, str, str], ...] = (
    ("exchange_flow", "rugbrain.exchange_flow", "exchange_flow_loop"),
    ("liquidation_heatmap", "rugbrain.liquidation_heatmap", "liquidation_scan_loop"),
    ("token_unlock_monitor", "rugbrain.token_unlock_monitor", "unlock_scan_loop"),
    ("market_health", "rugbrain.market_health", "market_health_loop"),
    ("hyperliquid_monitor", "rugbrain.monitors.hyperliquid_monitor", "hyperliquid_loop"),
    ("social_sentiment", "rugbrain.monitors.social_sentiment", "social_sentiment_loop"),
    ("twitter_tracker", "rugbrain.monitors.twitter_tracker", "twitter_tracker_loop"),
    ("defi_lending", "rugbrain.monitors.defi_lending", "defi_lending_loop"),
    ("token_distribution", "rugbrain.monitors.token_distribution", "token_distribution_loop"),
    ("content_digest", "rugbrain.monitors.content_digest", "content_digest_loop"),
    ("exchange_reserves", "rugbrain.monitors.exchange_reserves", "exchange_reserves_loop"),
    ("exchange_wallets", "rugbrain.monitors.exchange_wallets", "exchange_wallet_loop"),
    ("oracle_monitor", "rugbrain.monitors.oracle_monitor", "oracle_monitor_loop"),
    ("bucketshop_tracker", "rugbrain.monitors.bucketshop_tracker", "bucketshop_tracker_loop"),
)


def _managed_step(spec: ManagedSpec) -> BootStep:
    name, module, start_sym, running_sym, flag, stop_sym, stats = spec

    async def _run() -> bool:
        return await boot_runner.start_managed(module, start_sym, running_sym, flag, stats)

    async def _stop() -> None:
        await boot_runner.stop_managed(module, stop_sym)

    return BootStep(name=name, run=_run, stop=_stop)


def _bare_step(spec: tuple[str, str, str]) -> BootStep:
    name, module, symbol = spec

    async def _run() -> bool:
        ok = boot_runner.spawn_task(module, symbol)
        if ok:
            boot_runner.flag_tasks((name,))
        return ok

    return BootStep(name=name, run=_run)


async def schema_step() -> bool:
    """Validate/create the canonical Qdrant collections at the production dim."""
    base_url = os.getenv("QDRANT_URL", "http://rmi-qdrant-v2:6334").rstrip("/")
    return await probes.ensure_canonical_schema(base_url)


def _schema_boot_step() -> BootStep:
    async def _run() -> bool:
        ok = await schema_step()
        STATE["qdrant_schema"] = ok
        return ok

    return BootStep(name="qdrant_schema", run=_run)


BOOT_STEPS: tuple[BootStep, ...] = (
    tuple(_managed_step(s) for s in MANAGED)
    + tuple(_bare_step(s) for s in BARE_BEFORE)
    + (_schema_boot_step(),)
    + tuple(_bare_step(s) for s in BARE_AFTER)
)

#: Every subsystem name in boot order. Asserted against the pre-split list.
STEP_NAMES: tuple[str, ...] = tuple(s.name for s in BOOT_STEPS)

#: Systems with an explicit stop handle, in SHUTDOWN order. This deliberately
#: differs from boot order: the pre-split shutdown stopped the fear-index
#: poller first and the news updater last.
STOP_STEPS: tuple[BootStep, ...] = tuple(s for s in BOOT_STEPS if s.stop is not None)
STOP_STEPS = tuple(sorted(STOP_STEPS, key=lambda s: STOP_ORDER.index(s.name)))
STOP_NAMES: tuple[str, ...] = tuple(s.name for s in STOP_STEPS)

#: /health flags contributed by the bare scan loops, in the legacy order.
BARE_FLAG_NAMES: tuple[str, ...] = tuple(n for n, _, _ in BARE_BEFORE + BARE_AFTER)
