"""Risk-scoring feed: escalate token risk verdicts on a fearful market."""

from __future__ import annotations

from typing import Any

_RISK_ORDER: tuple[str, ...] = ("unknown", "low", "medium", "high", "critical")


def _escalate(risk: str, steps: int) -> str:
    index = _RISK_ORDER.index(risk) if risk in _RISK_ORDER else 0
    return _RISK_ORDER[min(index + steps, len(_RISK_ORDER) - 1)]


def adjust_risk_for_market(verdict: dict[str, Any], fear_value: int | None) -> dict[str, Any]:
    """Escalate a token risk verdict when the market is fearful.

    - ``fear_value < 25`` (extreme fear): +1 step for unknown/low/medium.
    - ``fear_value < 40`` (fear): +1 step for low risk.
    - otherwise unchanged.

    Adds ``fear_index`` and ``fear_adjusted`` to the returned copy, and
    appends ``fear_market_risk_boost`` to ``risk_signals`` when escalated.
    """
    out = dict(verdict)
    out["fear_index"] = fear_value
    risk = str(out.get("risk", "unknown"))
    steps = 0
    if fear_value is not None and fear_value < 25:
        steps = 1 if risk in ("unknown", "low", "medium") else 0
    elif fear_value is not None and fear_value < 40:
        steps = 1 if risk == "low" else 0

    if steps:
        out["risk"] = _escalate(risk, steps)
        out["fear_adjusted"] = True
        signals = list(out.get("risk_signals") or [])
        if "fear_market_risk_boost" not in signals:
            signals.append("fear_market_risk_boost")
        out["risk_signals"] = signals
    else:
        out["fear_adjusted"] = False
    return out
