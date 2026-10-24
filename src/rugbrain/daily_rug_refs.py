"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Token and chart references extracted from already-cited evidence.

A reference is a pointer, not a claim: a ticker appears only if a retrieved
snippet spelled it, and a chart appears only if a retrieved source lives on a
chart host. Both carry the citation id that justifies them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

CHART_HOSTS = ("dexscreener", "defillama", "tradingview", "coingecko", "dune")


def token_refs(evidence: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Unique tickers mentioned in citable snippets, with their source marker."""
    tickers: dict[str, dict[str, str]] = {}
    for row in evidence:
        for ticker in re.findall(r"\$([A-Z]{2,10})\b", row["snippet"]):
            tickers.setdefault(
                ticker,
                {"symbol": f"${ticker}", "source": f"[{row['id']}]", "url": row["url"]},
            )
    return list(tickers.values())


def chart_refs(evidence: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Evidence rows hosted on a charting or analytics domain."""
    refs: list[dict[str, str]] = []
    for row in evidence:
        host = urlparse(row["url"]).netloc.lower()
        if any(marker in host for marker in CHART_HOSTS):
            refs.append({"label": row["title"], "url": row["url"], "source": f"[{row['id']}]"})
    return refs
