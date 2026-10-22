"""Hyperliquid liquidation + whale-bet intelligence (keyless, read-only).

This package is an *analysis layer*, not an ingester: ``rugbrain.monitors
.hyperliquid_monitor`` already owns the polling loop, and running a second poller
would double the upstream load for the same data.  These functions turn payloads
the existing loop can obtain into attributable liquidation facts, whale
positions, and number-first content units.

Gap this package was written to close: the existing liquidation path requests
``{"type": "liquidationOrders"}``, which Hyperliquid answers with 422 — so it has
been returning an empty list every cycle.  Liquidations are only visible in
``userFills`` via the ``liquidation`` block; see ``liquidations.extract``.
"""

from __future__ import annotations

from rugbrain.hyperliquid_intel.cascade import summarise_cascade
from rugbrain.hyperliquid_intel.content import UNITS, render_unit
from rugbrain.hyperliquid_intel.endpoints import ENDPOINTS, request_for
from rugbrain.hyperliquid_intel.entries import recent_entries
from rugbrain.hyperliquid_intel.liquidations import extract_liquidations
from rugbrain.hyperliquid_intel.whales import largest_positions
from rugbrain.hyperliquid_intel.window import summarise_window

__all__ = [
    "ENDPOINTS",
    "UNITS",
    "extract_liquidations",
    "largest_positions",
    "recent_entries",
    "render_unit",
    "request_for",
    "summarise_cascade",
    "summarise_window",
]
