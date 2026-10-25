"""Evidence-based trending fusion.

Rank-normalise each signal INSIDE its own source, fuse with config weights, and
carry per-token evidence. Negative weights (paid placement, scanner risk) pull
scores down, so a token present only through a paid boost can never rank.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PAID_SIGNAL = "promotion_paid"
KNOWN_SIGNALS = {
    "volume_24h",
    "volume_accel",
    "liquidity",
    "holder_growth",
    "scanner_score",
    "social_mentions",
    "bonding_progress",
    "buy_pressure",
    "growth",
    "momentum_24h",
    "age",
    "risk_penalty",
    PAID_SIGNAL,
}
_DEFAULT_CFG = Path(__file__).resolve().parents[3] / "config" / "trending_weights.json"

def config_path() -> Path:
    """Resolve the weights file (env override, repo checkout, then cwd)."""
    override = os.environ.get("RUGBRAIN_TRENDING_WEIGHTS")
    if override:
        return Path(override)
    if _DEFAULT_CFG.exists():
        return _DEFAULT_CFG
    return Path.cwd() / "config" / "trending_weights.json"


def load_weights(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the weight file. Raises ValueError on any bad entry."""
    cfg = json.loads(Path(path or config_path()).read_text())
    signals = cfg.get("signals") or {}
    if not isinstance(signals, dict) or not signals:
        raise ValueError("weights: 'signals' must be a non-empty object")
    if PAID_SIGNAL not in signals:
        raise ValueError(f"weights: '{PAID_SIGNAL}' is required so paid placement can never rank as demand")
    for name, spec in signals.items():
        if name not in KNOWN_SIGNALS:
            raise ValueError(f"weights: unknown signal {name!r}")
        weight = spec.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            raise ValueError(f"weights: {name!r} needs a numeric weight")
        if not str(spec.get("rationale") or "").strip():
            raise ValueError(f"weights: {name!r} needs a one-line rationale - no magic numbers")
        if not spec.get("sources"):
            raise ValueError(f"weights: {name!r} needs at least one source")
        if name == PAID_SIGNAL and float(weight) > 0:
            raise ValueError(f"weights: {PAID_SIGNAL} must be zero or negative")
    if sum(float(s["weight"]) for n, s in signals.items() if n != PAID_SIGNAL and float(s["weight"]) > 0) <= 0:
        raise ValueError("weights: at least one positive-weight signal is required")
    return cfg


def _rank_scores(values: Mapping[Any, float]) -> dict[Any, float]:
    """Rank-normalise inside one source: best -> 1.0, worst -> 0.0."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ordered) == 1:
        return {ordered[0][0]: 1.0}
    return {key: 1.0 - (idx / (len(ordered) - 1)) for idx, (key, _) in enumerate(ordered)}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def fuse(
    rows: Iterable[Mapping[str, Any]],
    weights: dict[str, Any] | None = None,
    limit: int = 10,
    missing_sources: Mapping[str, str] | Iterable[str] = (),
) -> dict[str, Any]:
    """Fuse per-source rows into one ranked list with evidence.

    Each row: ``{source, chain, token_address, symbol, signals: {name: raw}, paid}``.
    """
    cfg = weights or load_weights()
    signals = cfg["signals"]
    positive = {n: float(s["weight"]) for n, s in signals.items() if float(s["weight"]) > 0}
    paid_weight = float(signals[PAID_SIGNAL]["weight"])

    tokens: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        chain, addr = row.get("chain"), row.get("token_address")
        if not chain or not addr:
            continue
        tok = tokens.setdefault(
            (chain, addr),
            {"chain": chain, "token_address": addr, "symbol": row.get("symbol") or "", "raw": {}, "paid": []},
        )
        if not tok["symbol"] and row.get("symbol"):
            tok["symbol"] = row["symbol"]
        for name, raw in (row.get("signals") or {}).items():
            if name not in signals:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            per_source = tok["raw"].setdefault(name, {})
            per_source[row["source"]] = max(per_source.get(row["source"], value), value)
        if row.get("paid"):
            tok["paid"].append(
                {
                    "source": row["source"],
                    "kind": row.get("paid_kind") or f"{row['source']}_paid",
                    "amount_usd": float(row.get("paid_amount") or 0.0),
                    "paid": True,
                }
            )

    normalised: dict[str, dict[tuple[str, str], float]] = {}
    coverage: dict[str, int] = {}
    for name in signals:
        per_source: dict[str, dict[tuple[str, str], float]] = {}
        for key, tok in tokens.items():
            for source, value in (tok["raw"].get(name) or {}).items():
                per_source.setdefault(source, {})[key] = value
        merged: dict[tuple[str, str], float] = {}
        for values in per_source.values():
            for key, score in _rank_scores(values).items():
                merged[key] = max(merged.get(key, 0.0), score)
        normalised[name] = merged
        coverage[name] = len(merged)

    # Denominator reflects only signals this run actually observed. A source that
    # was never fetched is reported as missing instead of being scored as zero.
    denominator = sum(weight for name, weight in positive.items() if coverage.get(name))

    ranked: list[dict[str, Any]] = []
    paid_only: list[dict[str, Any]] = []
    for key, tok in tokens.items():
        evidence = []
        for name, per_source in tok["raw"].items():
            for source, value in per_source.items():
                evidence.append(
                    {
                        "source": source,
                        "signal": name,
                        "value": value,
                        "normalised": round(normalised[name].get(key, 0.0), 6),
                        "paid": name == PAID_SIGNAL,
                    }
                )
        # Presence, not rank: the lowest-ranked token in a real source still has
        # organic evidence (rank 0.0), and only a paid-only token has none.
        organic = any(tok["raw"].get(name) for name in positive)
        base = (
            sum(positive[n] * normalised[n].get(key, 0.0) for n in positive if coverage.get(n)) / denominator
            if denominator
            else 0.0
        )
        penalty = sum(
            float(spec["weight"]) * normalised[name].get(key, 0.0)
            for name, spec in signals.items()
            if float(spec["weight"]) < 0 and coverage.get(name)
        )
        score = _clamp(base + penalty)
        item = {
            "chain": tok["chain"],
            "token_address": tok["token_address"],
            "symbol": tok["symbol"],
            "score": round(score, 6),
            "paid_only": not organic,
            "evidence": sorted(evidence, key=lambda e: (e["source"], e["signal"])),
            "disclosed_promotions": sorted(tok["paid"], key=lambda p: p["source"]),
        }
        (paid_only if item["paid_only"] else ranked).append(item)

    ranked.sort(key=lambda item: (-item["score"], item["chain"], item["token_address"]))
    paid_only.sort(key=lambda item: (-item["score"], item["token_address"]))
    for position, item in enumerate(ranked[: max(limit, 0)], start=1):
        item["rank"] = position
    return {
        "version": cfg.get("version"),
        "limit": limit,
        "ranked": ranked[: max(limit, 0)],
        "paid_only_excluded": paid_only,
        "missing_sources": dict(missing_sources) if isinstance(missing_sources, Mapping)
        else {name: "not wired" for name in missing_sources},
        "coverage": coverage,
        "paid_weight": paid_weight,
    }
