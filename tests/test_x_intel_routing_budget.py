"""Unit tests for x_intel routing, budget gating, and pacing thresholds."""

from __future__ import annotations

import time
from typing import Any

import pytest

from rugbrain.ingest_sources.x_intel.budget import BudgetTracker
from rugbrain.ingest_sources.x_intel.config import (
    GETXAPI_DAILY_CALL_LIMIT,
    GETXAPI_TOTAL_BUDGET,
    TIER1_ACCOUNTS,
)
from rugbrain.ingest_sources.x_intel.routing import route_account, should_use_getxapi


class FakeRedis:
    """Minimal async Redis stub (no monkeypatching — injected)."""

    def __init__(self, store: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = store if store is not None else {}

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

    def pipeline(self, transaction: bool = False) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, r: FakeRedis) -> None:
        self._r, self._ops = r, []

    def incr(self, key: str) -> FakePipeline:
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int) -> FakePipeline:
        self._ops.append(("expire", key))
        return self

    def incrbyfloat(self, key: str, amount: float) -> FakePipeline:
        self._ops.append(("incrbyfloat", key, amount))
        return self

    async def execute(self) -> list[Any]:
        out = []
        for op in self._ops:
            if op[0] == "incr":
                out.append(await self._r.incr(op[1]))
            elif op[0] == "incrbyfloat":
                out.append(await self._r.incrbyfloat(op[1], op[2]))
            else:
                out.append(True)
        return out


@pytest.mark.asyncio
async def test_route_account_tiers() -> None:
    assert route_account(TIER1_ACCOUNTS[0]) == "getxapi"
    assert route_account("zachxbt") == "getxapi"
    assert route_account("unknown_longtail_123") == "pry"
    assert route_account("@zachxbt") == "getxapi"


@pytest.mark.asyncio
async def test_routing_endpoint_rules() -> None:
    assert await should_use_getxapi("search") is True
    assert await should_use_getxapi("user-info") is True
    assert await should_use_getxapi("trends") is True
    assert await should_use_getxapi("user-mentions") is False
    assert await should_use_getxapi("user-tweets", {"followers_count": 100_000}) is True
    assert await should_use_getxapi("user-tweets", {"followers_count": 100}) is False
    assert await should_use_getxapi("user-tweets", {}) is True  # unknown → conservative


@pytest.mark.asyncio
async def test_pacing_override_sends_everything_to_pry() -> None:
    r = FakeRedis({"xintel:pacing_until": str(time.time() + 3600)})
    assert await should_use_getxapi("search", redis_client=r) is False
    assert await should_use_getxapi("user-info", redis_client=r) is False


@pytest.mark.asyncio
async def test_budget_blocks_at_daily_cap() -> None:
    r = FakeRedis({BudgetTracker._daily_key(): GETXAPI_DAILY_CALL_LIMIT})
    tracker = BudgetTracker(redis_client=r)
    assert await tracker.can_afford_call() is False


@pytest.mark.asyncio
async def test_budget_blocks_at_total_spend() -> None:
    r = FakeRedis({"xintel:spend": GETXAPI_TOTAL_BUDGET})
    assert await BudgetTracker(redis_client=r).can_afford_call() is False


@pytest.mark.asyncio
async def test_budget_allows_within_limits() -> None:
    r = FakeRedis({"xintel:spend": 0.05, "xintel:calls:20260101": 10})
    tracker = BudgetTracker(redis_client=r)
    assert await tracker.can_afford_call() is True


@pytest.mark.asyncio
async def test_record_call_meters_counters_and_spend() -> None:
    r = FakeRedis()
    tracker = BudgetTracker(redis_client=r)
    await tracker.record_call(endpoint="user-tweets")
    usage = await tracker.get_daily_usage()
    assert usage["calls"] == 1
    assert usage["hourly_calls"] == 1
    assert usage["spend"] == pytest.approx(0.001, abs=1e-6)


@pytest.mark.asyncio
async def test_pacing_engages_on_hourly_threshold() -> None:
    r = FakeRedis({BudgetTracker._hourly_key(): 51})
    tracker = BudgetTracker(redis_client=r)
    await tracker._check_pacing()
    assert float(r.store["xintel:pacing_until"]) > time.time()
