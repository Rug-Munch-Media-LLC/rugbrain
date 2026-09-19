"""Centralized exchange liquidation fetchers — re-exports from individual exchange modules."""

from __future__ import annotations

# Re-export all CEX fetchers for backward compatibility
from rugbrain.liquidation_heatmap.binance_fetcher import get_binance_liquidations
from rugbrain.liquidation_heatmap.bybit_fetcher import get_bybit_liquidations
from rugbrain.liquidation_heatmap.okx_fetcher import get_okx_liquidations
from rugbrain.liquidation_heatmap.hyperliquid_fetcher import get_hyperliquid_liquidations
from rugbrain.liquidation_heatmap.deribit_fetcher import get_deribit_liquidations
from rugbrain.liquidation_heatmap.other_cex_fetchers import (
    get_bitmex_liquidations,
    get_kraken_futures_liquidations,
    get_kucoin_futures_liquidations,
    get_phemex_liquidations,
)

__all__ = [
    "get_binance_liquidations",
    "get_bybit_liquidations",
    "get_okx_liquidations",
    "get_kraken_futures_liquidations",
    "get_bitmex_liquidations",
    "get_deribit_liquidations",
    "get_hyperliquid_liquidations",
    "get_kucoin_futures_liquidations",
    "get_phemex_liquidations",
]
