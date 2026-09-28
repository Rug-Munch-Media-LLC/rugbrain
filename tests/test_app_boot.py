"""Regression tests for the ``rugbrain.app`` split of ``main.py``.

The 2026-09-28 refactor turned a 472-line ``lifespan`` into a data table plus a
loop. These tests pin the two things that split could silently change: the boot
ORDER (the Qdrant schema check runs mid-sequence, not at the end) and the
shutdown ORDER (fear-index first, news updater last).
"""

from __future__ import annotations

import pytest

from rugbrain import main
from rugbrain.app.boot_steps import BARE_FLAG_NAMES, BOOT_STEPS, STOP_NAMES
from rugbrain.app.health_state import STATE, set_flag, snapshot

#: The exact sequence the pre-split ``lifespan`` executed, in order.
EXPECTED_BOOT_ORDER = (
    "scanner_subscriber",
    "scanner_poller",
    "fear_index_poller",
    "realtime_monitor",
    "news_story_updater",
    "mempool_scanner",
    "mev_detector",
    "governance_aggregator",
    "podcast_ingester",
    "smart_money_tracker",
    "youtube_ingester",
    "newsletter_ingester",
    "qdrant_schema",
    "exchange_flow",
    "liquidation_heatmap",
    "token_unlock_monitor",
    "market_health",
    "hyperliquid_monitor",
    "social_sentiment",
    "twitter_tracker",
    "defi_lending",
    "token_distribution",
    "content_digest",
    "exchange_reserves",
    "exchange_wallets",
    "oracle_monitor",
    "bucketshop_tracker",
)

#: Pre-split shutdown stopped the fear-index poller FIRST, news updater LAST.
EXPECTED_STOP_ORDER = (
    "fear_index_poller",
    "scanner_subscriber",
    "scanner_poller",
    "realtime_monitor",
    "news_story_updater",
)


def test_boot_order_preserved() -> None:
    """All 27 subsystems boot in the original order; schema check is 13th."""
    assert main.BOOT_STEP_NAMES == EXPECTED_BOOT_ORDER
    assert len(BOOT_STEPS) == 27


def test_schema_check_is_mid_sequence() -> None:
    """The Qdrant schema validation must not have drifted to the end."""
    assert main.BOOT_STEP_NAMES.index("qdrant_schema") == 12
    names = [s.name for s in BOOT_STEPS]
    assert names.index("newsletter_ingester") < names.index("qdrant_schema")
    assert names.index("qdrant_schema") < names.index("exchange_flow")


def test_shutdown_order_preserved() -> None:
    """Shutdown order is independent of boot order and is not alphabetical."""
    assert STOP_NAMES == EXPECTED_STOP_ORDER
    assert tuple(n for n in main.BOOT_STEP_NAMES if n in STOP_NAMES) != STOP_NAMES


def test_health_state_is_a_single_shared_object() -> None:
    """``main._health_state`` and ``app.health_state.STATE`` are one dict."""
    main._health_state["probe"] = "x"
    assert STATE["probe"] == "x"
    del STATE["probe"]


def test_snapshot_does_not_leak_the_live_dict() -> None:
    """A snapshot copy cannot mutate process health state."""
    set_flag("unit_test_flag", True)
    snap = snapshot()
    snap["unit_test_flag"] = False
    assert STATE["unit_test_flag"] is True
    del STATE["unit_test_flag"]


def test_bare_flag_names_cover_every_scan_loop() -> None:
    """Every bare scan loop reports a flag on /health; managed ones do not."""
    assert len(BARE_FLAG_NAMES) == 21
    assert "mempool_scanner" in BARE_FLAG_NAMES
    assert "scanner_poller" not in BARE_FLAG_NAMES


def test_boot_table_has_no_duplicate_names() -> None:
    """A duplicate name would silently overwrite one subsystem's health flag."""
    names = [s.name for s in BOOT_STEPS]
    assert len(names) == len(set(names))


def test_every_boot_step_has_a_runner() -> None:
    """A step with no callable ``run`` would be skipped silently at boot."""
    for step in BOOT_STEPS:
        assert callable(step.run), step.name


@pytest.mark.parametrize("step", BOOT_STEPS, ids=lambda s: s.name)
def test_boot_step_run_is_a_coroutine_function(step) -> None:  # type: ignore[no-untyped-def]
    """``run`` must be awaitable, not a bare function returning a coroutine."""
    import inspect

    assert inspect.iscoroutinefunction(step.run), step.name
