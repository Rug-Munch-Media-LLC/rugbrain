"""Payment-gate (x402 402) signal for the Pry scrape lane.

Pry's ``/v1/scrape`` is x402-gated: without a valid ``PRY_API_KEY`` bearer it
answers ``402 Payment Required``. That is NOT a transient outage — the lane is
structurally dead (no key, revoked key, or exhausted credits) and every cycle
fails identically. It must be loud:

* ``record_paywall`` bumps a Redis counter and logs at ERROR (the hard metric);
* ``PryLaneUnavailable`` propagates so the cycle runner exits nonzero.

Kept in its own module so ``pry_lane`` stays under the 200-line cap.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Redis counter for lifetime payment-gated scrape attempts.
PRY_402_COUNTER_KEY: str = "xintel:pry_lane_402_total"
#: Process exit code for a cycle that ended on a dead Pry lane (not 0/1).
PRY_LANE_EXIT_CODE: int = 3


class PryLaneUnavailable(RuntimeError):
    """Pry refused the scrape as payment-gated (x402 402) and produced nothing."""


async def record_paywall() -> None:
    """Bump the payment-gated counter, emit the hard metric, never raise.

    The ERROR line is emitted whether or not the counter lands. A Redis outage
    must not make the 402 silent again — that is the bug this module exists to
    kill, so the log is unconditional and only ``total`` degrades to ``unknown``.
    """
    total: int | None = None
    try:
        from rugbrain.compat import get_redis_async

        total = await (await get_redis_async()).incr(PRY_402_COUNTER_KEY)
    except Exception as exc:
        logger.debug("xintel:pry_402_counter_failed err=%s", str(exc)[:120])
    logger.error(
        "xintel:pry_lane_402_total=%s lane=dead key_invalid_or_credits_exhausted",
        "unknown" if total is None else total,
    )


__all__ = [
    "PRY_402_COUNTER_KEY",
    "PRY_LANE_EXIT_CODE",
    "PryLaneUnavailable",
    "record_paywall",
]
