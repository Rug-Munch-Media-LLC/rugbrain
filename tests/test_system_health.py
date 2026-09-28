"""Tests for rugbrain.system_health — aggregated health dashboard.

Covers probe degradation, SQL builders, zombie detection, and the full
aggregation with all external services mocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import rugbrain.system_health as sh

# ═══════════════════════════════════════════════════════════════════════
# SQL builders
# ═══════════════════════════════════════════════════════════════════════


def test_chain_state_sql_replaces_db() -> None:
    sql = sh._chain_state_sql("wallet_memory")
    assert "wallet_memory.evm_transfers" in sql
    assert "wallet_memory.solana_transfers" in sql
    assert "__DB__" not in sql


def test_chain_max_sql_replaces_db() -> None:
    sql = sh._chain_max_sql("rmi")
    assert "rmi.evm_transfers" in sql
    assert "__DB__" not in sql


# ═══════════════════════════════════════════════════════════════════════
# System section (pure /proc helpers)
# ═══════════════════════════════════════════════════════════════════════


def test_meminfo_returns_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16777216 kB\n"
        "MemAvailable:   8388608 kB\n"
        "SwapTotal:      8000000 kB\n"
        "SwapFree:       2000000 kB\n"
    )
    monkeypatch.setattr(sh, "_meminfo", lambda: _parse_meminfo(str(meminfo)))
    info = sh._meminfo()
    assert info["swap_used_pct"] == pytest.approx(75.0, rel=1e-3)
    assert info["swap_pressure"] == "normal"
    assert info["mem_used_pct"] == pytest.approx(50.0, rel=1e-3)


def _parse_meminfo(path: str) -> dict[str, Any]:
    fields: dict[str, int] = {}
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2 and parts[0].endswith(":"):
                fields[parts[0][:-1]] = int(parts[1])
    total = fields.get("MemTotal", 0)
    available = fields.get("MemAvailable", 0)
    swap_total = fields.get("SwapTotal", 0)
    swap_free = fields.get("SwapFree", 0)
    swap_used = swap_total - swap_free
    return {
        "mem_total_kb": total,
        "mem_available_kb": available,
        "mem_used_pct": round(100 * (total - available) / total, 1) if total else None,
        "swap_total_kb": swap_total,
        "swap_used_kb": swap_used,
        "swap_used_pct": round(100 * swap_used / swap_total, 1) if swap_total else None,
        "swap_pressure": "high" if swap_total and swap_used / swap_total > 0.8 else "normal",
    }


def test_meminfo_high_pressure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       1000000 kB\nMemAvailable:   500000 kB\n"
        "SwapTotal:      1000000 kB\nSwapFree:       100000 kB\n"
    )
    info = _parse_meminfo(str(meminfo))
    assert info["swap_used_pct"] == pytest.approx(90.0, rel=1e-3)
    assert info["swap_pressure"] == "high"


def test_zombies_scan(tmp_path: Path) -> None:
    procdir = tmp_path / "proc"
    procdir.mkdir()
    # Zombie process: state 'Z'
    zdir = procdir / "42"
    zdir.mkdir()
    (zdir / "stat").write_text("42 (zombie_proc) Z 1 42 42 0 -1 0 0 0\n")
    # Normal process: state 'S'
    ndir = procdir / "43"
    ndir.mkdir()
    (ndir / "stat").write_text("43 (normal_proc) S 1 43 43 0 -1 0 0 0\n")
    # Non-numeric dir ignored
    (procdir / "cpuinfo").write_text("x")
    assert sh._scan_proc_zombies(str(procdir)) == [42]
    # Missing stat files are skipped, not fatal.
    assert sh._scan_proc_zombies(str(tmp_path / "nope")) == []


# ═══════════════════════════════════════════════════════════════════════
# Section probes (mocked network)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rugbrain_section_all_down(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def down(url: Any, timeout_s: float | None = None) -> bool:
        return False

    monkeypatch.setattr(sh, "_http_ok", down)
    section = await sh._rugbrain_section()
    assert section["qdrant"]["ok"] is False
    assert section["embed"]["ok"] is False
    # B2 unset → ok is None (not configured), not False.
    assert section["b2"]["ok"] is None


@pytest.mark.asyncio
async def test_rugbrain_section_redis_ping_fails(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def down(url: Any, timeout_s: float | None = None) -> bool:
        return False

    monkeypatch.setattr(sh, "_http_ok", down)
    section = await sh._rugbrain_section()
    assert section["redis"]["ok"] is False


@pytest.mark.asyncio
async def test_indexer_section_ch_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sh, "_clickhouse_client", lambda: None)
    section = await sh._indexer_section()
    assert section["ok"] is False
    assert section["clickhouse"]["ok"] is False


@pytest.mark.asyncio
async def test_indexer_section_with_chains(monkeypatch: pytest.MonkeyPatch) -> Any:
    class FakeCh:
        def query(self, sql: Any, parameters: Any = None, settings: Any = None) -> Any:
            return _FakeRows([("ethereum", 22_000_000, "2026-08-04 12:00:00")])

        def close(self) -> None:
            pass

    monkeypatch.setattr(sh, "_clickhouse_client", lambda: FakeCh())
    section = await sh._indexer_section()
    assert section["ok"] is True
    assert section["chain_count"] == 1
    assert section["chains"][0]["chain"] == "ethereum"
    assert section["chains"][0]["latest_block"] == 22_000_000


class _FakeRows:
    def __init__(self, rows: Any) -> None:
        self.result_rows = rows


@pytest.mark.asyncio
async def test_scanner_section_ok_from_sentinel_alone(monkeypatch: pytest.MonkeyPatch) -> Any:
    class FakeRedis:
        async def ping(self) -> bool:
            return True

        def scan_iter(self, match: str | None = None, count: int | None = None) -> Any:
            async def _gen() -> Any:
                yield "sentinel_scanner"
                yield "rmi_databus_cache_hits_total"

            return _gen()

        async def aclose(self) -> None:
            pass

    import redis.asyncio as real_aioredis

    monkeypatch.setattr(real_aioredis, "Redis", lambda *a, **k: FakeRedis())
    section = await sh._scanner_section()
    assert section["ok"] is True
    assert "sentinel_scanner" in section["sentinel"]["modules"]
    # Standalone (2026-09-28): no rmi-backend probe. `ok` must follow the
    # sentinel/databus lanes alone, and no `backend` key may be reported.
    assert "backend" not in section


# ═══════════════════════════════════════════════════════════════════════
# Full aggregation
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_full_health_shape(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def ok_section() -> dict[str, Any]:
        return {"ok": True}

    async def ok_http(url: Any, timeout_s: float | None = None) -> bool:
        return True

    monkeypatch.setattr(sh, "_rugbrain_section", ok_section)
    monkeypatch.setattr(sh, "_indexer_section", ok_section)
    monkeypatch.setattr(sh, "_scanner_section", ok_section)
    monkeypatch.setattr(sh, "_system_section", lambda: {"ok": True})
    monkeypatch.setattr(sh, "_http_ok", ok_http)

    health = await sh.get_full_health()
    assert health["status"] == "ok"
    assert {"rugbrain", "indexer", "scanner", "system"} <= set(health.keys())
    assert "timestamp" in health


@pytest.mark.asyncio
async def test_get_full_health_degraded(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def ok_section() -> dict[str, Any]:
        return {"ok": True}

    async def bad_section() -> dict[str, Any]:
        return {"ok": False}

    monkeypatch.setattr(sh, "_rugbrain_section", ok_section)
    monkeypatch.setattr(sh, "_indexer_section", bad_section)
    monkeypatch.setattr(sh, "_scanner_section", ok_section)
    monkeypatch.setattr(sh, "_system_section", lambda: {"ok": True})

    health = await sh.get_full_health()
    assert health["status"] == "degraded"
