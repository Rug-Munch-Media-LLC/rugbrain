"""Unavailable-capability shims, moved out of `compat` to keep it under the limit.

Every symbol here replaces an `app.*` capability that standalone RugBrain has
not implemented natively yet. They are grouped by intent so a caller that
depends on one of them can be found by reading one small file.

Two policies, deliberately different:

* **Silent pass-through** (e.g. ``mmr_dedup_results``) degrades result quality
  quietly. Kept for now, but each is a documented capability gap — see the
  master next-and-needed. Prefer wiring the real implementation.
* **Fail loudly** (``score_deployer_risk``) is correct wherever a wrong answer
  would be a public claim about a real wallet. These raise so the HTTP layer can
  return 503 instead of fabricating a score.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException  # noqa: F401  (re-exported for callers)


# ── Deployer Reputation ───────────────────────────────────────────
class DeployerScorerUnavailable(RuntimeError):
    """No real deployer-risk scorer is wired (see score_deployer_risk)."""


def score_deployer_risk(address: str, chain: str = "ethereum", **kwargs: Any) -> dict[str, Any]:
    """Score deployer risk — unavailable standalone, fails loudly.

    Used to return ``{"risk_score": 0.5, "deployer_age_days": 0,
    "rugs_deployed": 0}``, which the HTTP layer surfaced as a real verdict:
    a fabricated accusation on a public endpoint. A real scorer arrives with
    the ``rug_events`` corpus (deployer → prior-rug history), not as a stub.
    """
    raise DeployerScorerUnavailable(
        "deployer-risk scoring is not implemented: it requires the rug_events "
        "corpus (deployer -> prior-rug history). Refusing to return a placeholder."
    )


# ── Cross-Chain Identity ─────────────────────────────────────────
def resolve_cross_chain_identity(address: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Resolve cross-chain identity — no-op standalone (returns [])."""
    return []


# ── Unified Scanner ──────────────────────────────────────────────
async def scan_wallet(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Scan a wallet — no-op standalone stub retained for import compatibility."""
    return {
        "address": address,
        "chain": chain,
        "risk_score": 0.5,
        "flags": [],
        "note": "wallet scanning unavailable standalone",
    }
