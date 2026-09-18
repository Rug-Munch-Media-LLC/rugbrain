"""Budget tracker for the metered GetXAPI lane — Redis-backed, fail-open.

Ported from rugmunchbot ``tools/getxapi_budget.py`` (accounting half), adjusted
to the live account: $4.65 ceiling, 150 calls/day hard cap, plan credits
expiring 2026-10-02.

Doctrine: ``can_afford_call()`` is checked BEFORE every HTTP call and
``record_call()`` meters after, so a call that would exceed the daily or
lifetime budget is never made. Pacing (hourly > 50 or daily > 400) switches
the lane to Pry-only until the window rolls over. Fail-open: Redis down →
calls allowed but unmetered (WARNING logged).

Redis keys (all ``xintel:``): ``calls:{YYYYMMDD}``,
``calls:hourly:{YYYYMMDDHH}``, ``spend`` (lifetime), ``pacing_until`` (epoch).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rugbrain.ingest_sources.x_intel.config import (
    GETXAPI_COST_PER_CALL,
    GETXAPI_DAILY_CALL_LIMIT,
    GETXAPI_TOTAL_BUDGET,
    HOURLY_CALL_LIMIT,
    PACING_DAILY_LIMIT,
)

logger = logging.getLogger(__name__)
_DAILY_KEY_TTL = 172800  # 48h — stale daily keys expire
_WARN_CALLS = 100
_CRIT_CALLS = 130
_CRIT_SPEND = 4.18  # 90% of $4.65

_warned_calls = False
_crit_calls = False
_crit_spend = False


def _today() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


def _hour() -> str:
    return time.strftime("%Y%m%d%H", time.gmtime())


class BudgetTracker:
    """Async Redis-backed budget ledger. Accepts an injected client for tests."""

    def __init__(self, redis_client: Any | None = None) -> None:
        self._injected = redis_client

    async def _redis(self) -> Any | None:
        """Configured Redis client, or None when down (fail-open)."""
        if self._injected is not None:
            return self._injected
        try:
            from rugbrain.compat import get_redis_async

            return await get_redis_async()
        except Exception as exc:
            logger.warning("xintel_budget:redis_unavailable err=%s", str(exc)[:120])
            return None

    @staticmethod
    def _daily_key() -> str:
        return f"xintel:calls:{_today()}"

    @staticmethod
    def _hourly_key() -> str:
        return f"xintel:calls:hourly:{_hour()}"

    async def record_call(self, endpoint: str = "", cost: float | None = None) -> None:
        """Meter one GetXAPI call: daily + hourly counters + lifetime spend."""
        cost = GETXAPI_COST_PER_CALL if cost is None else float(cost)
        r = await self._redis()
        if r is None:
            logger.warning("xintel_budget:record_failed_redis_down endpoint=%s", endpoint)
            return
        try:
            pipe = r.pipeline(transaction=False)
            pipe.incr(self._daily_key())
            pipe.expire(self._daily_key(), _DAILY_KEY_TTL)
            pipe.incr(self._hourly_key())
            pipe.expire(self._hourly_key(), _DAILY_KEY_TTL)
            pipe.incrbyfloat("xintel:spend", cost)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "xintel_budget:record_failed endpoint=%s err=%s", endpoint, str(exc)[:120]
            )
            return
        await self._check_thresholds()
        await self._check_pacing()

    async def _read_counters(self) -> tuple[int, float]:
        """(daily_calls, total_spend); (0, 0.0) when Redis is down."""
        r = await self._redis()
        if r is None:
            return 0, 0.0
        try:
            calls = int(await r.get(self._daily_key()) or 0)
            spend = float(await r.get("xintel:spend") or 0.0)
            return calls, spend
        except Exception as exc:
            logger.warning("xintel_budget:read_failed err=%s", str(exc)[:120])
            return 0, 0.0

    async def can_afford_call(self) -> bool:
        """False once the daily cap or lifetime dollar ceiling is reached."""
        calls, spend = await self._read_counters()
        if calls >= GETXAPI_DAILY_CALL_LIMIT:
            logger.warning(
                "xintel_budget:blocked_daily_limit calls=%s limit=%s",
                calls,
                GETXAPI_DAILY_CALL_LIMIT,
            )
            return False
        if spend >= GETXAPI_TOTAL_BUDGET:
            logger.warning(
                "xintel_budget:blocked_budget spend=%.4f budget=%.2f", spend, GETXAPI_TOTAL_BUDGET
            )
            return False
        return True

    async def get_daily_usage(self) -> dict[str, Any]:
        """Usage snapshot: calls, spend, remaining, pacing state."""
        calls, spend = await self._read_counters()
        r = await self._redis()
        hourly = 0
        pacing_until = 0.0
        if r is not None:
            try:
                hourly = int(await r.get(self._hourly_key()) or 0)
                pacing_until = float(await r.get("xintel:pacing_until") or 0.0)
            except Exception:
                pass
        budget_remaining = max(0.0, GETXAPI_TOTAL_BUDGET - spend)
        calls_remaining = max(0, GETXAPI_DAILY_CALL_LIMIT - calls)
        budget_calls = (
            int(budget_remaining // GETXAPI_COST_PER_CALL)
            if GETXAPI_COST_PER_CALL > 0
            else calls_remaining
        )
        return {
            "calls": calls,
            "hourly_calls": hourly,
            "spend": round(spend, 4),
            "budget_remaining": round(budget_remaining, 4),
            "calls_remaining": min(calls_remaining, budget_calls),
            "pacing_active": pacing_until > time.time(),
        }

    async def _check_thresholds(self) -> None:
        """Fire WARNING / CRITICAL once per crossing (not per call)."""
        global _warned_calls, _crit_calls, _crit_spend
        calls, spend = await self._read_counters()
        if calls > _WARN_CALLS and not _warned_calls:
            _warned_calls = True
            logger.warning("xintel_budget:approaching_daily_limit calls=%s", calls)
        if calls > _CRIT_CALLS and not _crit_calls:
            _crit_calls = True
            logger.critical("xintel_budget:daily_limit_nearly_exhausted calls=%s", calls)
        if spend > _CRIT_SPEND and not _crit_spend:
            _crit_spend = True
            logger.critical("xintel_budget:budget_nearly_exhausted spend=%.4f", spend)
    async def _check_pacing(self) -> None:
        """Engage Pry-only pacing when hourly > 50 or daily > 400."""
        usage, now, until = await self.get_daily_usage(), time.time(), 0.0
        if usage["hourly_calls"] > HOURLY_CALL_LIMIT:
            until = (int(now // 3600) + 1) * 3600
        if usage["calls"] > PACING_DAILY_LIMIT:
            until = max(until, (int(now // 86400) + 1) * 86400)
        if not until:
            return
        r = await self._redis()
        if r is None:
            return
        try:
            current = float(await r.get("xintel:pacing_until") or 0.0)
            if current < until:
                await r.set("xintel:pacing_until", str(until))
                logger.warning("xintel_budget:pacing_engaged pry_only_until=%s", until)
        except Exception as exc:
            logger.debug("xintel_budget:pacing_set_failed err=%s", str(exc)[:120])


_default = BudgetTracker()


def default_tracker() -> BudgetTracker:
    """Process-wide tracker (shared Redis counters)."""
    return _default


__all__ = ["BudgetTracker", "default_tracker"]
