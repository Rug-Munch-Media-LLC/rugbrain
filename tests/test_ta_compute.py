"""Bar guards and provenance for :func:`rugbrain.ta.compute.compute`.

The failure this file exists to prevent: an indicator computed over 12 bars
that claims a 200-period SMA. Every guard is asserted with its *counts*, so a
refusal carries the evidence a caller needs to widen the window — and a refusal
can never be mistaken for an answer with a short tail of ``None``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.ta.candles import Candle
from rugbrain.ta.compute import ComputeRequest, compute
from rugbrain.ta.series_hash import digest
from rugbrain.ta.specs import parse_specs, required_bars

START = datetime(2026, 10, 1, tzinfo=UTC)


def ramp(bars: int, step: float = 0.5) -> list[Candle]:
    """A deterministic ascending series with a real body on every bar."""
    out: list[Candle] = []
    for i in range(bars):
        close = 100.0 + step * i
        out.append(
            Candle(
                timestamp=START + timedelta(hours=i),
                open=close - step / 2,
                high=close + 1.0,
                low=close - step / 2 - 1.0,
                close=close,
                volume=100.0 + i,
            )
        )
    return out


def test_unknown_pair_is_not_guessed() -> None:
    """No rows -> ``unknown``, with no hash and no values."""
    result = compute(ComputeRequest("solana:doesnotexist", candles=[]))
    assert result.status == "unknown"
    assert result.bars == 0
    assert result.series_hash is None
    assert result.values == {}


def test_insufficient_bars_reports_the_counts() -> None:
    """12 bars cannot answer SMA(200); say how many are missing."""
    result = compute(
        ComputeRequest("solana:abc", window=12, indicators=["sma:200"], candles=ramp(12))
    )
    assert result.status == "insufficient_data"
    assert result.bars == 12
    assert result.required_bars == 200
    assert result.short == (("sma(200)", 200),)
    assert result.values == {}
    assert "12 bars" in result.detail


def test_only_the_short_indicators_are_named() -> None:
    """A mixed request names the short one and not the satisfied ones."""
    result = compute(
        ComputeRequest(
            "solana:abc", window=30, indicators=["sma:20", "rsi:14", "sma:50"], candles=ramp(30)
        )
    )
    assert result.status == "insufficient_data"
    assert result.short == (("sma(50)", 50),)


def test_ok_path_carries_values_hash_and_as_of() -> None:
    """A successful answer is reproducible: rows hashed, last bar timestamped."""
    bars = ramp(70)
    result = compute(
        ComputeRequest("solana:abc", window=70, indicators=["rsi:14", "macd"], candles=bars)
    )
    assert result.status == "ok"
    assert result.bars == 70
    assert result.series_hash == digest(bars)
    assert result.series_hash.startswith("sha256:")
    assert result.as_of == bars[-1].timestamp.isoformat()
    assert set(result.values) == {
        "rsi(14)",
        "macd(12,26,9)",
        "macd(12,26,9).signal",
        "macd(12,26,9).hist",
    }
    assert all(len(series) == 70 for series in result.values.values())


def test_values_align_with_their_bar() -> None:
    """Value ``i`` belongs to bar ``i`` — the alignment contract, spot-checked."""
    bars = ramp(70)
    result = compute(ComputeRequest("solana:abc", window=70, indicators=["sma:20"], candles=bars))
    series = result.values["sma(20)"]
    assert series[-1] == pytest.approx(sum(b.close for b in bars[-20:]) / 20)
    assert series[18] is None
    assert series[19] is not None


def test_unsupported_timeframe_refuses_to_resample() -> None:
    """The substrate is hourly; a 4h request is a no, not a silent resample."""
    result = compute(
        ComputeRequest("solana:abc", timeframe="4h", indicators=["rsi:14"], candles=ramp(50))
    )
    assert result.status == "unsupported_timeframe"
    assert result.values == {}


def test_hash_is_deterministic_and_revision_sensitive() -> None:
    """Same rows -> same hash; one revised close -> a different hash."""
    bars = ramp(40)
    assert digest(bars) == digest(ramp(40))
    bars[7] = Candle(
        timestamp=bars[7].timestamp,
        open=bars[7].open,
        high=bars[7].high,
        low=bars[7].low,
        close=bars[7].close + 0.01,
        volume=bars[7].volume,
    )
    assert digest(bars) != digest(ramp(40))


def test_bad_input_fails_loudly() -> None:
    """A typo is a caller bug: no zeros, no silently dropped indicator."""
    with pytest.raises(ValueError, match="chain:pair_address"):
        compute(ComputeRequest("solana", candles=ramp(40)))
    with pytest.raises(ValueError, match="unknown indicator"):
        compute(ComputeRequest("solana:abc", indicators=["rsii"], candles=ramp(40)))
    with pytest.raises(ValueError, match="duplicate"):
        compute(ComputeRequest("solana:abc", indicators=["rsi", "rsi:14"], candles=ramp(40)))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rsi", 15),
        ("rsi:21", 22),
        ("atr:14", 15),
        ("sma:200", 200),
        ("ema:20", 20),
        ("bbands:20:2", 20),
        ("macd:12:26:9", 34),
        ("cdl_engulfing", 10),
    ],
)
def test_required_bars_matches_the_reference_warmup(text: str, expected: int) -> None:
    """Bar floors are the reference implementation's warm-up, not a guess."""
    (spec,) = parse_specs([text])
    assert required_bars(spec) == expected


def test_latest_is_the_quoted_number() -> None:
    """``latest()`` is what a report cites, and it is the last aligned value."""
    result = compute(
        ComputeRequest("solana:abc", window=60, indicators=["sma:20"], candles=ramp(60))
    )
    assert result.latest()["sma(20)"] == result.values["sma(20)"][-1]
