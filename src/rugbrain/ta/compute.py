"""Indicator computation over a real OHLCV window, with provenance attached.

Takes ``(pair, timeframe, window, indicators)`` and returns indicator values
**plus the hash of the exact bars and the as-of timestamp those values came
from**. A report can then say "RSI(14) = 71.2 at 2026-10-24T20:00Z over
sha256:1f3c…", which is a claim someone else can re-run.

Three refusals, never a silent substitution:

``unknown``
    No rows for the pair. We do not fall back to a similarly-named pair.
``insufficient_data``
    Fewer bars than the requested indicators consume. The answer carries the
    count it had, the count it needed, and which indicators were short — a
    200-period SMA reported over 12 bars is a lie, so it is not reported.
``unsupported_timeframe``
    The substrate is hourly only (``wallet_memory.dex_ohlcv_hourly``). Rather
    than resample silently, say no.

The candle source is a dependency-injection seam (``candles=``) so the guards
are testable with no database, and the read itself stays in
:mod:`rugbrain.ta.reader` — this module never touches ClickHouse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import talib_engine
from .candles import Candle
from .reader import CandleReadError, PairRef, fetch_candles
from .series_hash import digest
from .specs import parse_specs, required_bars

#: The only timeframe the OHLCV table stores. Resampling is a separate job.
SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("1h",)

#: Default lookback in bars. 200 covers SMA(200) with no warm-up shortfall.
DEFAULT_WINDOW = 200


@dataclass(frozen=True, slots=True)
class TaComputation:
    """Indicator values + the identity of the bars that produced them."""

    pair: str
    timeframe: str
    status: str
    bars: int
    required_bars: int
    indicators: tuple[str, ...]
    timestamps: tuple[str, ...] = ()
    values: dict[str, list[float | None]] = field(default_factory=dict)
    series_hash: str | None = None
    as_of: str | None = None
    short: tuple[tuple[str, int], ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view — the shape a route or a report would publish."""
        return {
            "pair": self.pair,
            "timeframe": self.timeframe,
            "status": self.status,
            "bars": self.bars,
            "required_bars": self.required_bars,
            "indicators": list(self.indicators),
            "as_of": self.as_of,
            "series_hash": self.series_hash,
            "values": self.values,
            "short": [{"indicator": name, "required": n} for name, n in self.short],
            "detail": self.detail,
        }

    def latest(self) -> dict[str, float | None]:
        """The most recent value of every output — the number an answer quotes."""
        out: dict[str, float | None] = {}
        for key, series in self.values.items():
            out[key] = series[-1] if series else None
        return out


def compute(
    pair: str | PairRef,
    timeframe: str = "1h",
    window: int = DEFAULT_WINDOW,
    indicators: Sequence[str] | str = ("rsi",),
    *,
    candles: Sequence[Candle] | None = None,
    as_of: datetime | None = None,
) -> TaComputation:
    """Compute *indicators* for *pair*, or say precisely why we cannot."""
    specs = parse_specs(indicators)
    need = max(required_bars(spec) for spec in specs)
    ref = _pair_ref(pair)
    base = TaComputation(
        pair=str(ref),
        timeframe=timeframe,
        status="ok",
        bars=0,
        required_bars=need,
        indicators=tuple(spec.label for spec in specs),
    )
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _replace(
            base, status="unsupported_timeframe", detail=f"supported: {SUPPORTED_TIMEFRAMES}"
        )

    rows = list(candles) if candles is not None else _read(ref, window, as_of)
    if rows is None:
        return _replace(base, status="read_error", detail="ohlcv read failed; see reader logs")
    if not rows:
        return _replace(base, status="unknown", detail=f"no hourly bars for {ref}")

    short = tuple((s.label, required_bars(s)) for s in specs if required_bars(s) > len(rows))
    if short:
        return _replace(
            base,
            status="insufficient_data",
            bars=len(rows),
            short=short,
            detail=f"{len(rows)} bars available, {need} required",
        )

    try:
        values = talib_engine.run(specs, rows)
    except talib_engine.TalibUnavailable as exc:
        return _replace(base, status="engine_unavailable", bars=len(rows), detail=str(exc))

    return _replace(
        base,
        bars=len(rows),
        timestamps=tuple(c.timestamp.isoformat() for c in rows),
        values=values,
        series_hash=digest(rows),
        as_of=rows[-1].timestamp.isoformat(),
    )


def _read(ref: PairRef, window: int, as_of: datetime | None) -> list[Candle] | None:
    try:
        return list(fetch_candles(ref, window, end=as_of))
    except CandleReadError:
        return None


def _pair_ref(pair: str | PairRef) -> PairRef:
    if isinstance(pair, PairRef):
        return pair
    chain, _, address = str(pair).partition(":")
    if not chain or not address:
        raise ValueError(f"pair must be 'chain:pair_address', got {pair!r}")
    return PairRef(chain=chain, pair_address=address)


def _replace(base: TaComputation, **changes: Any) -> TaComputation:
    merged = {f: getattr(base, f) for f in base.__dataclass_fields__}
    merged.update(changes)
    return TaComputation(**merged)


__all__ = ["DEFAULT_WINDOW", "SUPPORTED_TIMEFRAMES", "TaComputation", "compute"]
