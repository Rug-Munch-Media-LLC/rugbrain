"""Liquidation Heatmap — tracks total market liquidations, per-exchange, wins, hype liquidations.

Tracks:
1. **Total market liquidations** (24h, 7d, 30d) — across all sources
2. **Per-exchange liquidations** — Binance, Bybit, OKX, dYdX, GMX, Synthetix, Kraken Futures, BitMEX, Deribit, Aave, Compound, MakerDAO
3. **Liquidation wins** — profitable liquidator transactions (who profited?)
4. **Hype liquidation tracking** — large positions from known influencers/whales
5. **24h aggregated volume** in USD with historical comparison
6. **Alerting** — >$1M, >$10M, >$100M events sent to Telegram

Sources: Binance Futures API, Bybit API, OKX API, dYdX API, GMX subgraph,
Synthetix subgraph, Kraken Futures API, BitMEX API, Deribit API,
Etherscan logs (Aave, Compound, MakerDAO), DeFiLlama, CoinGecko.
"""

from __future__ import annotations

# Re-export all public names for backward compatibility
from rugbrain.liquidation_heatmap.constants import (
    CEX_LIQUIDATION_ENDPOINTS,
    ETHERSCAN_KEY,
    KNOWN_LIQUIDATORS,
    LIQUIDATION_CONTRACTS,
    POLL_INTERVAL,
)
from rugbrain.liquidation_heatmap.cex_fetchers import (
    get_binance_liquidations,
    get_bitmex_liquidations,
    get_bybit_liquidations,
    get_deribit_liquidations,
    get_hyperliquid_liquidations,
    get_kraken_futures_liquidations,
    get_kucoin_futures_liquidations,
    get_okx_liquidations,
    get_phemex_liquidations,
)
from rugbrain.liquidation_heatmap.protocol_fetchers import (
    get_dydx_liquidations,
    get_gmx_liquidations,
    get_protocol_liquidations,
    get_synthetix_liquidations,
)
from rugbrain.liquidation_heatmap.analysis import (
    get_hype_liquidations,
    get_liquidation_wins,
    get_total_market_liq_24h,
)
from rugbrain.liquidation_heatmap.orchestration import (
    liquidation_scan_cycle,
    liquidation_scan_loop,
)

__all__ = [
    "CEX_LIQUIDATION_ENDPOINTS",
    "ETHERSCAN_KEY",
    "KNOWN_LIQUIDATORS",
    "LIQUIDATION_CONTRACTS",
    "POLL_INTERVAL",
    "get_binance_liquidations",
    "get_bitmex_liquidations",
    "get_bybit_liquidations",
    "get_deribit_liquidations",
    "get_dydx_liquidations",
    "get_gmx_liquidations",
    "get_hyperliquid_liquidations",
    "get_kraken_futures_liquidations",
    "get_kucoin_futures_liquidations",
    "get_okx_liquidations",
    "get_phemex_liquidations",
    "get_protocol_liquidations",
    "get_synthetix_liquidations",
    "get_hype_liquidations",
    "get_liquidation_wins",
    "get_total_market_liq_24h",
    "liquidation_scan_cycle",
    "liquidation_scan_loop",
]
