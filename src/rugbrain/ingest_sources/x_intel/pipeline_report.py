"""Cycle reporting: one place that decides what a cycle's numbers MEAN.

Split out of ``pipeline`` (which was over the 200-line cap) so the orchestrator
only sequences stages. A cycle that fetched posts and ingested none is either a
quiet corpus (nothing new), a dead embedding lane, or a blocked write — those
are different operator actions, so the summary carries ``reason`` instead of
leaving ``ingested=0`` to be read as "all good".
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("rugbrain.x_intel")


def finalize(summary: dict[str, Any], pry_lane_dead: bool) -> dict[str, Any]:
    """Attach the cycle's reason, log it, and raise when the Pry lane is dead.

    ``reason`` is empty when the cycle did what it could. A ``PryLaneUnavailable``
    (x402 402 with nothing scraped) is structural, so it propagates and the
    runner exits nonzero rather than reporting a green empty cycle.
    """
    from rugbrain.ingest_sources.x_intel.pry_lane import PryLaneUnavailable

    if not summary.get("reason"):
        if summary.get("fetched") and not summary.get("unique"):
            summary["reason"] = "all_duplicate_or_dust"
        elif summary.get("unique") and not summary.get("ingested"):
            summary["reason"] = summary.get("store_reason") or "embedded_none"
    if pry_lane_dead:
        summary["pry_lane_dead"] = True
        logger.error(
            "xintel:pry_lane_dead pry_handles=%s fetched=%s action=check_PRY_API_KEY_and_x402_credits",
            summary.get("pry_handles"),
            summary.get("fetched"),
        )
    logger.info(
        "xintel_cycle_done fetched=%s unique=%s ingested=%s failed=%s skipped=%s reason=%s",
        summary.get("fetched"),
        summary.get("unique"),
        summary.get("ingested"),
        summary.get("failed"),
        summary.get("skipped"),
        summary.get("reason") or "-",
    )
    if pry_lane_dead:
        raise PryLaneUnavailable(
            f"pry lane payment-gated (402) for all {summary.get('pry_handles')} handles"
        )
    return summary


__all__ = ["finalize"]
