"""Edge-case tests for x_intel budget fail-open, pacing, and usage shape.

Extends ``test_x_intel_routing_budget.py`` (FakeRedis/fixtures conventions) —
covers only the edges the base suite misses: Redis-down fail-open, daily
pacing threshold, usage-shape invariants, and routing normalization.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from rugbrain.ingest_sources.x_intel.budget import BudgetTracker
from rugbrain.ingest_sources.x_intel.config import (
    GETXAPI_COST_PER_CALL,
    GETXAPI_DAILY_CALL_LIMIT,
    HOURLY_CALL_LIMIT,
    PACING_DAILY_LIMIT,
)
from rugbrain.ingest_sources.x_intel.routing import (
    pacing_active,
    route_account,
    should_use_getxapi,
)


class BrokenRedis:
    """Redis stub that raises on every op — simulates Redis being down."""

    async def get(self, key: str) -> Any:
        raise ConnectionError("redis down")

    async def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> bool:
        raise ConnectionError("redis down")

    def pipeline(self, transaction: bool = False) -> Any:
        raise ConnectionError("redis down")


class TestBudgetFailOpen:
    """Redis down → calls allowed but unmetered, no exceptions escape."""

    @pytest.mark.asyncio
    async def test_can_afford_call_fails_open_when_redis_down(self) -> None:
        tracker = BudgetTracker(redis_client=BrokenRedis())
        assert await tracker.can_afford_call() is True

    @pytest.mark.asyncio
    async def test_record_call_swallows_redis_down(self, caplog: pytest.LogCaptureFixture) -> None:
        tracker = BudgetTracker(redis_client=BrokenRedis())
        await tracker.record_call(endpoint="search")  # must not raise
        assert any("record_failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_get_daily_usage_zeroed_when_redis_down(self) -> None:
        usage = await BudgetTracker(redis_client=BrokenRedis()).get_daily_usage()
        assert usage["calls"] == 0
        assert usage["spend"] == 0.0
        assert usage["pacing_active"] is False


class TestPacingThresholds:
    """Hourly > 50 / daily > 400 → pacing engaged → pry-only routing."""

    @pytest.mark.asyncio
    async def test_pacing_engages_on_daily_threshold(self) -> None:
        r: dict[str, Any] = {}
        tracker = BudgetTracker(redis_client=_DictRedis(r))
        r[BudgetTracker._daily_key()] = PACING_DAILY_LIMIT + 1
        await tracker._check_pacing()
        assert float(r["xintel:pacing_until"]) > time.time()
        # daily pacing must extend past the current day boundary
        assert float(r["xintel:pacing_until"]) >= (int(time.time() // 86400) + 1) * 86400

    @pytest.mark.asyncio
    async def test_no_pacing_below_thresholds(self) -> None:
        r: dict[str, Any] = {}
        r[BudgetTracker._daily_key()] = PACING_DAILY_LIMIT
        r[BudgetTracker._hourly_key()] = HOURLY_CALL_LIMIT
        tracker = BudgetTracker(redis_client=_DictRedis(r))
        await tracker._check_pacing()
        assert "xintel:pacing_until" not in r  # at-limit (not >) does not pace

    @pytest.mark.asyncio
    async def test_pacing_routes_all_endpoints_to_pry(self) -> None:
        r: dict[str, Any] = {}
        r[BudgetTracker._hourly_key()] = HOURLY_CALL_LIMIT + 1
        tracker = BudgetTracker(redis_client=_DictRedis(r))
        await tracker._check_pacing()
        assert await should_use_getxapi("search", redis_client=_DictRedis(r)) is False
        assert (
            await should_use_getxapi(
                "user-tweets", {"followers_count": 10**6}, redis_client=_DictRedis(r)
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_pacing_active_fail_open_false(self) -> None:
        assert await pacing_active(BrokenRedis()) is False


class TestUsageShape:
    """get_daily_usage() invariants."""

    @pytest.mark.asyncio
    async def test_usage_keys_and_remaining_math(self) -> None:
        r = _DictRedis(
            {
                BudgetTracker._daily_key(): 10,
                BudgetTracker._hourly_key(): 3,
                "xintel:spend": 0.01,
            }
        )
        usage = await BudgetTracker(redis_client=r).get_daily_usage()
        assert set(usage) == {
            "calls",
            "hourly_calls",
            "spend",
            "budget_remaining",
            "calls_remaining",
            "pacing_active",
        }
        assert usage["calls"] == 10
        assert usage["hourly_calls"] == 3
        assert usage["budget_remaining"] == pytest.approx(4.64, abs=1e-3)
        assert usage["calls_remaining"] == GETXAPI_DAILY_CALL_LIMIT - 10

    @pytest.mark.asyncio
    async def test_calls_remaining_capped_by_budget(self) -> None:
        # spend leaves room for fewer calls than the daily cap allows
        spend = 4.648  # $0.002 left of the $4.65 ceiling → 2 calls at $0.001
        r = _DictRedis({"xintel:spend": spend, BudgetTracker._daily_key(): 5})
        usage = await BudgetTracker(redis_client=r).get_daily_usage()
        assert usage["calls_remaining"] == int(0.002 // GETXAPI_COST_PER_CALL)

    @pytest.mark.asyncio
    async def test_record_call_custom_cost(self) -> None:
        r = _DictRedis({})
        tracker = BudgetTracker(redis_client=r)
        await tracker.record_call(endpoint="search", cost=0.5)
        usage = await tracker.get_daily_usage()
        assert usage["spend"] == pytest.approx(0.5, abs=1e-6)
        assert usage["calls"] == 1


class TestRoutingEdges:
    """Normalization + unknown-value edges missed by the base suite."""

    def test_route_account_empty_and_none(self) -> None:
        assert route_account("") == "pry"
        assert route_account("@") == "pry"

    @pytest.mark.asyncio
    async def test_endpoint_slash_normalization(self) -> None:
        assert await should_use_getxapi("user/tweets", {"followers_count": 100}) is False
        assert await should_use_getxapi("USER-INFO") is True

    @pytest.mark.asyncio
    async def test_user_tweets_non_numeric_followers_conservative(self) -> None:
        assert await should_use_getxapi("user-tweets", {"followers_count": "lots"}) is True


class _DictRedis:
    """Synchronous-dict-backed async Redis stub (pipeline-free subset)."""

    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key) or 0) + 1
        return int(self.store[key])

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key) or 0.0) + amount
        return float(self.store[key])

    def pipeline(self, transaction: bool = False) -> _DictPipeline:
        return _DictPipeline(self)


class _DictPipeline:
    def __init__(self, r: _DictRedis) -> None:
        self._r, self._ops = r, []

    def incr(self, key: str) -> _DictPipeline:
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int) -> _DictPipeline:
        return self

    def incrbyfloat(self, key: str, amount: float) -> _DictPipeline:
        self._ops.append(("incrbyfloat", key, amount))
        return self

    async def execute(self) -> list[Any]:
        out = []
        for op in self._ops:
            if op[0] == "incr":
                out.append(await self._r.incr(op[1]))
            else:
                out.append(await self._r.incrbyfloat(op[1], op[2]))
        return out
