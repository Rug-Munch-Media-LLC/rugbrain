"""A content hash of exactly the bars an answer was computed from.

One job: turn a candle series into one short, stable string a report can cite.

``sha256:1f3c…`` answers the only question that matters when two TA answers
disagree — *did they read the same rows?* The hash covers each bar's open time
and its six numeric fields, in order, so a re-ingest that revises a close, a
shifted window, or a different pair's series all produce a different digest.
Formatting is fixed (ISO-8601 UTC timestamps, ``repr`` floats) so the hash does
not drift with platform float formatting.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from .candles import Candle

#: Truncation length. 16 hex chars = 64 bits: collision-free for our row counts
#: and short enough to read in a report.
_DIGEST_CHARS = 16


def digest(candles: Sequence[Candle]) -> str:
    """``sha256:<16 hex>`` over the ordered bars, or ``sha256:empty`` for none."""
    if not candles:
        return "sha256:empty"
    hasher = hashlib.sha256()
    for candle in candles:
        hasher.update(_row(candle).encode("utf-8"))
    return f"sha256:{hasher.hexdigest()[:_DIGEST_CHARS]}"


def _row(candle: Candle) -> str:
    return (
        f"{candle.timestamp.isoformat()}|{candle.open!r}|{candle.high!r}|"
        f"{candle.low!r}|{candle.close!r}|{candle.volume!r}|{candle.trades}\n"
    )


__all__ = ["digest"]
