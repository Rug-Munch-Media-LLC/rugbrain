#!/usr/bin/env python3
"""
rugbrain-mcp — FastMCP facade over RugBrain HTTP API (127.0.0.1:8012).

Routing rule (internal):
  code/protocol/exploit-pattern → context-crypto
  news/scam/wallet/incident/sentiment → rugbrain-mcp
  chain state → rugmunch-data

Does not deprecate context-crypto's query_rugbrain yet — plan:
  1. rugbrain-mcp ships with rugbrain_* tool prefix (this build)
  2. 2-week shadow period: agents use rugbrain_* tools, compare quality
  3. After validation: flip default routing in guide_lookup, mark query_rugbrain deprecated
  4. Remove query_rugbrain from context-crypto in a follow-up PR
"""

from __future__ import annotations

from rugbrain.rugbrain_mcp import (
    market_helpers as market_helpers,
)
from rugbrain.rugbrain_mcp import (
    tools_intel as tools_intel,
)
from rugbrain.rugbrain_mcp import (
    tools_market as tools_market,
)
from rugbrain.rugbrain_mcp import (
    tools_search as tools_search,
)
from rugbrain.rugbrain_mcp.app import (
    API_KEY as API_KEY,
)
from rugbrain.rugbrain_mcp.app import (
    BASE as BASE,
)
from rugbrain.rugbrain_mcp.app import (
    BASE_URL as BASE_URL,
)
from rugbrain.rugbrain_mcp.app import (
    HEADERS as HEADERS,
)
from rugbrain.rugbrain_mcp.app import (
    TIMEOUT as TIMEOUT,
)
from rugbrain.rugbrain_mcp.app import (
    MultiSearchRequest as MultiSearchRequest,
)
from rugbrain.rugbrain_mcp.app import (
    _api_key as _api_key,
)
from rugbrain.rugbrain_mcp.app import (
    _call as _call,
)
from rugbrain.rugbrain_mcp.app import (
    log as log,
)
from rugbrain.rugbrain_mcp.app import (
    mcp as mcp,
)
from rugbrain.rugbrain_mcp.market_helpers import (
    _call_search_multi as _call_search_multi,
)
from rugbrain.rugbrain_mcp.market_helpers import (
    _extract_results as _extract_results,
)
from rugbrain.rugbrain_mcp.market_helpers import (
    _source_of as _source_of,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_alerts as rugbrain_alerts,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_analyze as rugbrain_analyze,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_health as rugbrain_health,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_news as rugbrain_news,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_scam_check as rugbrain_scam_check,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_sentiment as rugbrain_sentiment,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_vector_landscape as rugbrain_vector_landscape,
)
from rugbrain.rugbrain_mcp.tools_intel import (
    rugbrain_wallet_profile as rugbrain_wallet_profile,
)
from rugbrain.rugbrain_mcp.tools_market import (
    rugbrain_market_brief as rugbrain_market_brief,
)
from rugbrain.rugbrain_mcp.tools_market import (
    rugbrain_scam_radar as rugbrain_scam_radar,
)
from rugbrain.rugbrain_mcp.tools_market import (
    rugbrain_social_sentiment as rugbrain_social_sentiment,
)
from rugbrain.rugbrain_mcp.tools_search import (
    rugbrain_search as rugbrain_search,
)
from rugbrain.rugbrain_mcp.tools_search import (
    rugbrain_search_multi as rugbrain_search_multi,
)

__all__ = [
    "API_KEY",
    "BASE",
    "BASE_URL",
    "HEADERS",
    "TIMEOUT",
    "MultiSearchRequest",
    "_api_key",
    "_call",
    "_call_search_multi",
    "_extract_results",
    "_source_of",
    "log",
    "market_helpers",
    "mcp",
    "rugbrain_alerts",
    "rugbrain_analyze",
    "rugbrain_health",
    "rugbrain_market_brief",
    "rugbrain_news",
    "rugbrain_scam_check",
    "rugbrain_scam_radar",
    "rugbrain_search",
    "rugbrain_search_multi",
    "rugbrain_sentiment",
    "rugbrain_social_sentiment",
    "rugbrain_vector_landscape",
    "rugbrain_wallet_profile",
    "tools_intel",
    "tools_market",
    "tools_search",
]


def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    mcp.run()
