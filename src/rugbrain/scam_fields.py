"""Per-field parsing for aggregating raw threat-intel docs into scam patterns.

One job each: read a funds-lost value that arrives in three different shapes,
and pick the highest severity out of a list. Both are pure functions over
untrusted input, so both refuse rather than guess.
"""

from __future__ import annotations

import re
from typing import Any

from rugbrain.scam_vocab import SEVERITY_RANK

__all__ = ["max_severity", "parse_funds_lost"]

# The single-letter suffix must NOT be followed by another letter. Without that
# guard the regex matched the leading B of a TOKEN TICKER and read it as
# "billions":
#   '15114 BUSD' -> 15114 x 1e9 = $15,114,000,000,000  (observed 2026-09-28)
#   '~ 50 BNB'   -> 50   x 1e9 = $50,000,000,000
#   '3.3 BNB'    -> $3.3bn
# so a single BUSD loss was reported as 15 TRILLION dollars, and it was the
# top-ranked pattern in the whole collection. BNB and BUSD are both real,
# common tokens in this corpus, so the bug fired constantly and always upward.
# The guard is ONLY on the single-letter forms; the spelled-out forms are
# matched explicitly, because putting (?![A-Za-z]) on a group that also covers
# "billion" broke '2.5 billion' -> 2.
_FUNDS_RE = re.compile(r"([\d,\.]+)\s*(billion|million|thousand|[KMB](?![A-Za-z]))?", re.IGNORECASE)

_MULTIPLIERS = {"K": 1_000, "THOUSAND": 1_000, "M": 1_000_000, "MILLION": 1_000_000,
                "B": 1_000_000_000, "BILLION": 1_000_000_000}


def parse_funds_lost(value: Any) -> float:
    """Parse a funds-lost value (USD float, ``"$1.5M"`` string, or None) -> float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    match = _FUNDS_RE.search(text)
    if not match:
        return 0.0
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    return number * _MULTIPLIERS.get((match.group(2) or "").upper(), 1.0)


def max_severity(severities: list[str]) -> str:
    """Return the highest severity from a list of severity labels."""
    best = "info"
    best_rank = -1
    for sev in severities:
        rank = SEVERITY_RANK.get(sev.strip().lower(), 0)
        if rank > best_rank:
            best_rank = rank
            best = sev.strip().lower()
    return best
