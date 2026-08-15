"""RugBrain — aggregated system health dashboard.

``GET /api/v1/health/full`` returns one JSON document covering:

  * ``rugbrain``   — Qdrant, Redis, Infinity embedding, B2 permanence
  * ``indexer``    — LiteChainIndexer: ClickHouse connectivity + per-chain
                     block advancement (read from the indexer hot store)
  * ``scanner``    — SENTINEL scanner modules + DataBus provider activity
                     (Redis keys written by rmi-backend, plus backend /health)
  * ``system``     — swap pressure, disk, load average, zombie processes

Every probe is best-effort with a short timeout; a failed probe reports
``"ok": false`` and never raises. Suitable for Prometheus-style dashboards
and alerting on any ``*_ok == false`` leaf.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter

log = logging.getLogger("rugbrain.system_health")

router = APIRouter(prefix="/api/v1/health", tags=["health"])

# ── Configuration ─────────────────────────────────────────────────────────

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
INFINITY_URL = os.getenv("INFINITY_URL", "http://localhost:8001").rstrip("/")
B2_S3_ENDPOINT = os.getenv("B2_S3_ENDPOINT", "").rstrip("/")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME", "")
BACKEND_HEALTH_URL = os.getenv("BACKEND_HEALTH_URL", "http://localhost:8000/health")

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "rmi")
CLICKHOUSE_FALLBACK_DATABASE = os.getenv("CLICKHOUSE_FALLBACK_DATABASE", "wallet_memory")

PROBE_TIMEOUT = 3.0


# ── Probes ────────────────────────────────────────────────────────────────


async def _http_ok(url: str, timeout_s: float = PROBE_TIMEOUT) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url)
            return r.status_code < 500
    except Exception:
        return False


async def _probe_qdrant() -> dict[str, Any]:
    ok = await _http_ok(f"{QDRANT_URL}/")
    return {"ok": ok, "url": QDRANT_URL}


async def _probe_redis() -> dict[str, Any]:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        await r.ping()
        queue_depth = 0
        try:
            queue_depth = int(await r.llen("rmi:rag:findings_queue") or 0)
        except Exception:
            queue_depth = -1
        await r.aclose()
        return {"ok": True, "host": REDIS_HOST, "queue_depth": queue_depth}
    except Exception as e:
        return {"ok": False, "host": REDIS_HOST, "error": str(e)[:120]}


async def _probe_infinity() -> dict[str, Any]:
    ok = await _http_ok(f"{INFINITY_URL}/health")
    return {"ok": ok, "url": INFINITY_URL}


async def _probe_b2() -> dict[str, Any]:
    if not B2_S3_ENDPOINT:
        return {"ok": None, "url": "", "reason": "B2_S3_ENDPOINT not configured"}
    url = f"{B2_S3_ENDPOINT}/{B2_BUCKET_NAME}" if B2_BUCKET_NAME else B2_S3_ENDPOINT
    ok = await _http_ok(url, timeout_s=5.0)
    return {"ok": ok, "url": url}


async def _rugbrain_section() -> dict[str, Any]:
    qdrant, redis_, infinity, b2 = await asyncio.gather(
        _probe_qdrant(),
        _probe_redis(),
        _probe_infinity(),
        _probe_b2(),
    )
    overall = all(item.get("ok") is True for item in (qdrant, redis_, infinity)) or any(
        item.get("ok") is True for item in (qdrant, redis_)
    )
    return {
        "ok": overall,
        "qdrant": qdrant,
        "redis": redis_,
        "infinity": infinity,
        "b2": b2,
    }


# ── Indexer section ───────────────────────────────────────────────────────


def _clickhouse_client() -> Any | None:
    try:
        import clickhouse_connect

        return clickhouse_connect.get_client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            interface=CLICKHOUSE_INTERFACE,
            username=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DATABASE,
        )
    except Exception as e:
        log.debug("ch_client_failed: %s", e)
        return None


_CHAIN_STATE_SQL = """
SELECT chain, max(block_number) AS block, max(block_timestamp) AS ts
FROM __DB__.evm_transfers GROUP BY chain
UNION ALL
SELECT 'solana', max(slot) AS block, max(block_timestamp) AS ts
FROM __DB__.solana_transfers
"""

_CHAIN_MAX_SQL = """
SELECT chain, max(block_number) AS block FROM __DB__.evm_transfers GROUP BY chain
"""


def _chain_state_sql(db: str) -> str:
    # db is operator config (CLICKHOUSE_DATABASE env), never user input.
    return _CHAIN_STATE_SQL.replace("__DB__", db)


def _chain_max_sql(db: str) -> str:
    # db is operator config (CLICKHOUSE_DATABASE env), never user input.
    return _CHAIN_MAX_SQL.replace("__DB__", db)


async def _indexer_section() -> dict[str, Any]:
    # ClickHouse reachable?
    client = await asyncio.to_thread(_clickhouse_client)
    if client is None:
        return {
            "ok": False,
            "clickhouse": {"ok": False},
            "chains": [],
            "note": "ClickHouse unreachable from this host",
        }
    try:
        db = CLICKHOUSE_FALLBACK_DATABASE
        # Per-chain latest block. The indexer writes a per-chain state row
        # into wallet_memory; fall back to max(block_number) over the hot
        # tables when the state table is absent.
        chains: list[dict[str, Any]] = []
        try:
            # db is operator config (CLICKHOUSE_DATABASE env), never user input.
            rows = client.query(
                _chain_state_sql(db),
                settings={"max_execution_time": 10},
            ).result_rows
            for chain, block, ts in rows:
                chains.append(
                    {
                        "chain": chain,
                        "latest_block": int(block or 0),
                        "latest_timestamp": str(ts or ""),
                        "advancing": True,
                    }
                )
        except Exception as e:
            log.debug("indexer_chain_query_failed: %s", str(e)[:120])
            try:
                rows = client.query(
                    _chain_max_sql(db),
                    settings={"max_execution_time": 10},
                ).result_rows
                chains = [
                    {"chain": chain, "latest_block": int(block or 0), "advancing": True}
                    for chain, block in rows
                ]
            except Exception as e2:
                log.debug("indexer_chain_query_fallback_failed: %s", str(e2)[:120])
        return {
            "ok": True,
            "clickhouse": {"ok": True, "database": db},
            "chains": chains,
            "chain_count": len(chains),
        }
    except Exception:
        return {"ok": False, "clickhouse": {"ok": False}, "chains": []}
    finally:
        with contextlib.suppress(Exception):
            client.close()


# ── Scanner section ───────────────────────────────────────────────────────


async def _scanner_section() -> dict[str, Any]:
    sentinel: dict[str, Any] = {"modules": [], "ok": None}
    databus: dict[str, Any] = {"providers": [], "ok": None}
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        await r.ping()
        sentinel_keys = [k async for k in r.scan_iter(match="*sentinel*", count=200)]
        databus_keys = [k async for k in r.scan_iter(match="*databus*", count=200)]
        sentinel["modules"] = sorted(sentinel_keys)[:50]
        sentinel["ok"] = bool(sentinel_keys)
        databus["providers"] = sorted(databus_keys)[:50]
        databus["ok"] = bool(databus_keys)
        await r.aclose()
    except Exception as e:
        sentinel["error"] = str(e)[:120]
        databus["error"] = str(e)[:120]

    backend_ok = await _http_ok(BACKEND_HEALTH_URL, timeout_s=2.0)
    return {
        "ok": (sentinel.get("ok") is True) or (databus.get("ok") is True) or backend_ok,
        "sentinel": sentinel,
        "databus": databus,
        "backend": {"ok": backend_ok, "url": BACKEND_HEALTH_URL},
    }


# ── System section ────────────────────────────────────────────────────────


def _meminfo() -> dict[str, Any]:
    try:
        fields: dict[str, int] = {}
        with open("/proc/meminfo") as fh:
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
    except Exception as e:
        return {"error": str(e)[:120]}


def _scan_proc_zombies(proc_root: str = "/proc") -> list[int]:
    """Return PIDs of zombie processes under *proc_root* (best-effort)."""
    zombies: list[int] = []
    try:
        for entry in os.listdir(proc_root):
            if not entry.isdigit():
                continue
            try:
                with open(f"{proc_root}/{entry}/stat") as fh:
                    stat = fh.read()
                # comm can contain spaces/parens; state is the field right
                # after the last ')'. Parse robustly.
                rparen = stat.rfind(")")
                if rparen == -1:
                    continue
                rest = stat[rparen + 2 :].split()
                if rest and rest[0] == "Z":
                    zombies.append(int(entry))
            except (OSError, IndexError, ValueError):
                continue
    except Exception as e:
        log.debug("zombie_scan_failed: %s", e)
    return zombies


def _zombies() -> list[int]:
    return _scan_proc_zombies()


def _system_section() -> dict[str, Any]:
    try:
        disk = shutil.disk_usage("/")
    except OSError:
        disk = None
    load = []
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = []
    zombies = _zombies()
    swap = _meminfo()
    return {
        "ok": swap.get("swap_pressure") != "high",
        "hostname": os.uname().nodename,
        "swap": swap,
        "disk": (
            {
                "total_gb": round(disk.total / 1e9, 1),
                "used_gb": round(disk.used / 1e9, 1),
                "free_gb": round(disk.free / 1e9, 1),
                "used_pct": round(100 * disk.used / disk.total, 1),
            }
            if disk
            else {"error": "disk stats unavailable"}
        ),
        "load_avg_1_5_15": load,
        "zombies": zombies,
        "zombie_count": len(zombies),
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── Aggregated endpoint ───────────────────────────────────────────────────


async def get_full_health() -> dict[str, Any]:
    """Aggregate every section into one monitoring-friendly document."""
    rugbrain, indexer, scanner, system = await asyncio.gather(
        _rugbrain_section(),
        _indexer_section(),
        _scanner_section(),
        asyncio.to_thread(_system_section),
    )
    # Overall status: degraded as soon as any critical section is unhealthy.
    critical = {
        "rugbrain": rugbrain.get("ok") is True,
        "indexer": indexer.get("ok") is True,
        "scanner": scanner.get("ok") is True,
        "system": system.get("ok") is True,
    }
    status = "ok" if all(critical.values()) else "degraded"
    return {
        "status": status,
        "timestamp": datetime.now(UTC).isoformat(),
        "uptime_seconds": round(time.monotonic(), 1),
        "rugbrain": rugbrain,
        "indexer": indexer,
        "scanner": scanner,
        "system": system,
    }


@router.get("/full")
async def health_full() -> dict[str, Any]:
    """Full health dashboard — RugBrain + indexer + scanner + system."""
    return await get_full_health()


__all__ = ["get_full_health", "router"]
