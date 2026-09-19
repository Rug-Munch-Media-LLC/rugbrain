"""Constants and in-memory state for liquidation heatmap tracking."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Protocol liquidation contracts ─────────────────────────────────────────
LIQUIDATION_CONTRACTS: dict[str, str] = {
    "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9": "Aave V2",
    "0x87870bca3f3fd6335c3f4ce8392cc693010e6db9": "Aave V3",
    "0x3d9819210a31b4961b30ef54be2aed79b9c9cd3b": "Compound",
    "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2": "MakerDAO MCD",
}

# CEX liquidation endpoints (free, no auth)
CEX_LIQUIDATION_ENDPOINTS: dict[str, str] = {
    "Binance": "https://fapi.binance.com/fapi/v1/allLiquidationOrders?limit=100",
    "Bybit": "https://api.bybit.com/v5/market/liquidation-info?symbol=BTCUSDT&limit=50",
    "dYdX": "https://api.dydx.exchange/v3/markets",
    "GMX": "https://api.gmx.io",
    "Synthetix": "https://api.thegraph.com/subgraphs/name/synthetix",
    "Kraken Futures": "https://futures.kraken.com/derivatives/api/v3",
    "BitMEX": "https://www.bitmex.com/api/v1/liquidation",
    "Deribit": "https://www.deribit.com/api/v2/public/get_last_settlements",
    "KuCoin Futures": "https://api-futures.kucoin.com/api/v1/contracts/liquidation",
    "Phemex": "https://api.phemex.com/md/liquidation",
}

# Known liquidators (wallets that profit from liquidations)
KNOWN_LIQUIDATORS: dict[str, str] = {
    "0x6374eac0933c0fb6e5e5c5c0e4e1e5f5a5b5c5d5": "Wintermute Liquidator",
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": "Coinbase Liquidator",
    "0x8217e4b2c2c5b2c5e5f5a5b5c5d5e5f5a5b5c5d": "Jump Trading Liquidator",
    "0x0000000000000000000000000000000000000000": "Unknown",
}

ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "TZBW87IP5KHU1R7F4S3ZZZ5B2IM2RAS1V7")
POLL_INTERVAL = int(os.getenv("LIQUIDATION_POLL_INTERVAL", "120"))  # 2 min

# In-memory stores
_liquidation_history: list[dict[str, Any]] = []
_24h_volume: float = 0.0
_7d_volume: float = 0.0
_30d_volume: float = 0.0
_peak_24h: float = 0.0
_biggest_single: dict[str, Any] = {}
_seen_liquidations: set[str] = set()
