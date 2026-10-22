"""Extract OFAC-sanctioned digital-currency addresses from SDN Remarks text.

OFAC publishes crypto addresses inside the free-text ``Remarks`` field in a fixed
grammar: ``Digital Currency Address - <TICKER> <ADDRESS>``, repeated and usually
prefixed with ``alt.`` for alternates. Parsing the marker (rather than regex-hunting
base58 strings) avoids false positives from SWIFT/BIC codes and entity names.
"""

from __future__ import annotations

import re

MARKER = re.compile(r"Digital Currency Address\s*-\s*([A-Z]{2,6})\s+([A-Za-z0-9]{20,110})")
# OFAC ticker -> our chain label (XBT is the ISO code OFAC uses for Bitcoin).
TICKER_MAP = {"XBT": "BTC", "BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "XMR": "XMR",
              "LTC": "LTC", "XRP": "XRP", "TRX": "TRX", "USDT": "USDT", "ZEC": "ZEC"}


def extract_digital_currency(remarks: str) -> list[dict[str, str]]:
    """Return [{address, chain, ticker}] for every labelled address in ``remarks``."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for ticker, address in MARKER.findall(remarks or ""):
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "address": address,
                "address_lower": key,
                "chain": TICKER_MAP.get(ticker, ticker),
                "ticker": ticker,
            }
        )
    return found
