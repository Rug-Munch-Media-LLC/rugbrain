"""Cache backend — Redis adapter + in-memory fallback.

Replaces the private monorepo's ``app.core.redis`` usage inside the RAG
pipeline. Every portable module that touched Redis (dedup hashes, rate
limiter counters, ANN persistence, feedback records, embed cache) now goes
through :func:`get_cache`, which returns a :class:`CacheBackend`:

- :class:`LocalCache` — process-local in-memory store (default). All the
  dedup/rate-limit/feedback state is per-process; no external service.
- :class:`RedisCache` — thin async-friendly wrapper over a **synchronous**
  ``redis.Redis`` client (the call surface the ported modules use is sync),
  enabled when ``RAG_REDIS_URL`` is set and the ``redis`` package is
  installed.

The surface mirrors the subset of the redis-py API the ported modules
actually call: strings (``get``/``set``/``setex``/``delete``/``incr``),
sets (``sismember``/``sadd``/``scard``), sorted sets
(``zadd``/``zscore``/``zrem``/``zcard``/``zremrangebyscore``), hashes
(``hget``/``hset``/``hgetall``/``hincrby``), a minimal pipeline, and
``expire``/``ping``. Every method is fail-open: ``RedisCache`` catches
connection errors and degrades to no-op/empty results (matching the
private code's "fail-open when Redis is down" contract).
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from rugbrain.settings import settings

__all__ = ["CacheBackend", "LocalCache", "RedisCache", "get_cache"]


@runtime_checkable
class CacheBackend(Protocol):
    """Minimal cache surface used by the ported RAG modules (sync calls)."""

    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int = 0) -> None: ...
    def setex(self, key: str, ttl: int, value: Any) -> None: ...
    def delete(self, key: str, *keys: str) -> bool: ...
    def incr(self, key: str, amount: int = 1) -> int: ...
    def expire(self, key: str, ttl: int) -> bool: ...
    def ping(self) -> bool: ...
    # sets
    def sismember(self, key: str, member: Any) -> bool: ...
    def sadd(self, key: str, *members: Any) -> int: ...
    def scard(self, key: str) -> int: ...
    # sorted sets
    def zadd(self, key: str, mapping: dict[Any, float]) -> int: ...
    def zscore(self, key: str, member: Any) -> float | None: ...
    def zrem(self, key: str, *members: Any) -> int: ...
    def zcard(self, key: str) -> int: ...
    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int: ...
    # hashes
    def hget(self, key: str, field: str) -> Any | None: ...
    def hset(self, key: str, field: str, value: Any) -> int: ...
    def hgetall(self, key: str) -> dict[Any, Any]: ...
    def hdel(self, key: str, *fields: str) -> int: ...
    def hincrby(self, key: str, field: str, amount: int = 1) -> int: ...
    # pipeline
    def pipeline(self) -> CachePipeline: ...
    def scan(self, cursor: int = 0, match: str = "", count: int = 50) -> tuple[int, list[str]]: ...


class CachePipeline(Protocol):
    """Minimal redis-py compatible pipeline (subset used by ported code)."""

    def hset(self, key: str, field: str, value: Any) -> CachePipeline: ...
    def hdel(self, key: str, *fields: str) -> CachePipeline: ...
    def set(self, key: str, value: Any) -> CachePipeline: ...
    def sadd(self, key: str, *members: Any) -> CachePipeline: ...
    def setex(self, key: str, ttl: int, value: Any) -> CachePipeline: ...
    def zadd(self, key: str, mapping: dict[Any, float]) -> CachePipeline: ...
    def expire(self, key: str, ttl: int) -> CachePipeline: ...
    def execute(self) -> list[Any]: ...


class LocalCache:
    """Process-local in-memory cache with TTL support.

    Implements the full :class:`CacheBackend` surface so the ported modules
    behave identically without any external service. TTL is lazily enforced
    on read (entries past their expiry are treated as missing and pruned).
    """

    def __init__(self) -> None:
        self._strings: dict[str, tuple[float, Any]] = {}
        self._sets: dict[str, dict[Any, None]] = {}
        self._zsets: dict[str, dict[Any, float]] = {}
        self._hashes: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {}

    # ── strings ──────────────────────────────────────────────────────
    def _expired(self, entry: tuple[float, Any]) -> bool:
        expires_at, _ = entry
        return expires_at != 0.0 and time.monotonic() > expires_at

    def get(self, key: str) -> Any | None:
        entry = self._strings.get(key)
        if entry is None:
            return None
        if self._expired(entry):
            self._strings.pop(key, None)
            return None
        return entry[1]

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        self._strings[key] = (time.monotonic() + ttl if ttl else 0.0, value)

    def setex(self, key: str, ttl: int, value: Any) -> None:
        self.set(key, value, ttl=ttl)

    def delete(self, key: str, *keys: str) -> bool:
        removed = key in self._strings or bool(keys)
        for k in (key, *keys):
            self._strings.pop(k, None)
            self._sets.pop(k, None)
            self._zsets.pop(k, None)
            self._hashes.pop(k, None)
            self._counters.pop(k, None)
        return removed

    def incr(self, key: str, amount: int = 1) -> int:
        self._counters[key] = self._counters.get(key, 0) + amount
        return self._counters[key]

    def expire(self, key: str, ttl: int) -> bool:
        if key in self._strings:
            self._strings[key] = (time.monotonic() + ttl, self._strings[key][1])
            return True
        return False

    def ping(self) -> bool:
        return True

    # ── sets ─────────────────────────────────────────────────────────
    def sismember(self, key: str, member: Any) -> bool:
        return member in self._sets.get(key, {})

    def sadd(self, key: str, *members: Any) -> int:
        bucket = self._sets.setdefault(key, {})
        added = 0
        for m in members:
            if m not in bucket:
                bucket[m] = None
                added += 1
        return added

    def scard(self, key: str) -> int:
        return len(self._sets.get(key, {}))

    # ── sorted sets ──────────────────────────────────────────────────
    def zadd(self, key: str, mapping: dict[Any, float]) -> int:
        bucket = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in bucket:
                added += 1
            bucket[member] = float(score)
        return added

    def zscore(self, key: str, member: Any) -> float | None:
        return self._zsets.get(key, {}).get(member)

    def zrem(self, key: str, *members: Any) -> int:
        bucket = self._zsets.get(key)
        if not bucket:
            return 0
        removed = 0
        for m in members:
            if bucket.pop(m, None) is not None:
                removed += 1
        return removed

    def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        bucket = self._zsets.get(key)
        if not bucket:
            return 0
        to_drop = [m for m, s in bucket.items() if min_score <= s <= max_score]
        for m in to_drop:
            del bucket[m]
        return len(to_drop)

    # ── hashes ───────────────────────────────────────────────────────
    def hget(self, key: str, field: str) -> Any | None:
        return self._hashes.get(key, {}).get(field)

    def hset(self, key: str, field: str, value: Any) -> int:
        bucket = self._hashes.setdefault(key, {})
        new_field = field not in bucket
        bucket[field] = value
        return 1 if new_field else 0

    def hgetall(self, key: str) -> dict[Any, Any]:
        return dict(self._hashes.get(key, {}))

    def hdel(self, key: str, *fields: str) -> int:
        bucket = self._hashes.get(key)
        if not bucket:
            return 0
        removed = 0
        for f in fields:
            if bucket.pop(f, None) is not None:
                removed += 1
        return removed

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        bucket = self._hashes.setdefault(key, {})
        current = int(bucket.get(field, 0))
        bucket[field] = current + amount
        return current + amount

    # ── pipeline / scan ─────────────────────────────────────────────
    def pipeline(self) -> CachePipeline:
        return _LocalPipeline(self)

    def scan(self, cursor: int = 0, match: str = "", count: int = 50) -> tuple[int, list[str]]:
        """Iterate string keys matching the glob (subset: ``*`` suffix/pre/suffix).

        Returns ``(0, keys)`` — the in-memory store has no chunked cursor
        semantics, so one pass returns everything and the next cursor is 0.
        """
        keys = list(self._strings.keys())
        if match:
            pat = match.replace("*", ".*").replace("?", ".")
            import re as _re

            rx = _re.compile(f"^{pat}$")
            keys = [k for k in keys if rx.match(k)]
        return 0, keys


class _LocalPipeline:
    """Buffers operations and applies them in order on ``execute()``."""

    def __init__(self, cache: LocalCache) -> None:
        self._cache = cache
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def hset(self, key: str, field: str, value: Any) -> _LocalPipeline:
        self._ops.append(("hset", (key, field, value), {}))
        return self

    def hdel(self, key: str, *fields: str) -> _LocalPipeline:
        self._ops.append(("hdel", (key, *fields), {}))
        return self

    def set(self, key: str, value: Any) -> _LocalPipeline:
        self._ops.append(("set", (key, value), {}))
        return self

    def sadd(self, key: str, *members: Any) -> _LocalPipeline:
        self._ops.append(("sadd", (key, *members), {}))
        return self

    def setex(self, key: str, ttl: int, value: Any) -> _LocalPipeline:
        self._ops.append(("setex", (key, ttl, value), {}))
        return self

    def zadd(self, key: str, mapping: dict[Any, float]) -> _LocalPipeline:
        self._ops.append(("zadd", (key, mapping), {}))
        return self

    def expire(self, key: str, ttl: int) -> _LocalPipeline:
        self._ops.append(("expire", (key, ttl), {}))
        return self

    def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, args, _kwargs in self._ops:
            results.append(getattr(self._cache, op)(*args))
        self._ops = []
        return results


class RedisCache:
    """Sync redis-py client behind the CacheBackend surface (fail-open).

    If the ``redis`` package is missing or the connection is down, every
    method degrades to the documented default (``None`` / ``0`` / ``False`` /
    no-op) — never raises — mirroring the private ``app.core.redis`` contract.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None
        try:
            import redis as redis_module

            self._client = redis_module.from_url(
                url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception:
            self._client = None

    def _ok(self) -> bool:
        return self._client is not None

    # ── strings ──────────────────────────────────────────────────────
    def get(self, key: str) -> Any | None:
        try:
            return self._client.get(key) if self._ok() else None
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        try:
            if not self._ok():
                return
            if ttl:
                self._client.setex(key, ttl, value)
            else:
                self._client.set(key, value)
        except Exception:
            pass

    def setex(self, key: str, ttl: int, value: Any) -> None:
        self.set(key, value, ttl=ttl)

    def delete(self, key: str, *keys: str) -> bool:
        try:
            return bool(self._client.delete(key, *keys)) if self._ok() else False
        except Exception:
            return False

    def incr(self, key: str, amount: int = 1) -> int:
        try:
            return int(self._client.incr(key, amount)) if self._ok() else 0
        except Exception:
            return 0

    def expire(self, key: str, ttl: int) -> bool:
        try:
            return bool(self._client.expire(key, ttl)) if self._ok() else False
        except Exception:
            return False

    def ping(self) -> bool:
        try:
            return bool(self._client.ping()) if self._ok() else False
        except Exception:
            return False

    # ── sets ─────────────────────────────────────────────────────────
    def sismember(self, key: str, member: Any) -> bool:
        try:
            return bool(self._client.sismember(key, member)) if self._ok() else False
        except Exception:
            return False

    def sadd(self, key: str, *members: Any) -> int:
        try:
            return int(self._client.sadd(key, *members)) if self._ok() else 0
        except Exception:
            return 0

    def scard(self, key: str) -> int:
        try:
            return int(self._client.scard(key)) if self._ok() else 0
        except Exception:
            return 0

    # ── sorted sets ──────────────────────────────────────────────────
    def zadd(self, key: str, mapping: dict[Any, float]) -> int:
        try:
            return int(self._client.zadd(key, mapping)) if self._ok() else 0
        except Exception:
            return 0

    def zscore(self, key: str, member: Any) -> float | None:
        try:
            val = self._client.zscore(key, member) if self._ok() else None
            return float(val) if val is not None else None
        except Exception:
            return None

    def zrem(self, key: str, *members: Any) -> int:
        try:
            return int(self._client.zrem(key, *members)) if self._ok() else 0
        except Exception:
            return 0

    def zcard(self, key: str) -> int:
        try:
            return int(self._client.zcard(key)) if self._ok() else 0
        except Exception:
            return 0

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        try:
            return int(self._client.zremrangebyscore(key, min_score, max_score)) if self._ok() else 0
        except Exception:
            return 0

    # ── hashes ───────────────────────────────────────────────────────
    def hget(self, key: str, field: str) -> Any | None:
        try:
            return self._client.hget(key, field) if self._ok() else None
        except Exception:
            return None

    def hset(self, key: str, field: str, value: Any) -> int:
        try:
            return int(self._client.hset(key, field, value)) if self._ok() else 0
        except Exception:
            return 0

    def hgetall(self, key: str) -> dict[str, Any]:
        try:
            return dict(self._client.hgetall(key)) if self._ok() else {}
        except Exception:
            return {}

    def hdel(self, key: str, *fields: str) -> int:
        try:
            return int(self._client.hdel(key, *fields)) if self._ok() else 0
        except Exception:
            return 0

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        try:
            return int(self._client.hincrby(key, field, amount)) if self._ok() else 0
        except Exception:
            return 0

    # ── pipeline / scan ─────────────────────────────────────────────
    def pipeline(self) -> CachePipeline:
        try:
            if self._ok():
                return _RedisPipeline(self._client.pipeline())
        except Exception:
            pass
        return _LocalPipeline(LocalCache())

    def scan(self, cursor: int = 0, match: str = "", count: int = 50) -> tuple[int, list[str]]:
        try:
            if not self._ok():
                return 0, []
            result = self._client.scan(cursor=cursor, match=match or None, count=count)
            return (int(result[0]), list(result[1]))
        except Exception:
            return 0, []


class _RedisPipeline:
    """Delegates to redis-py's pipeline (fail-open on execute)."""

    def __init__(self, pipe: Any) -> None:
        self._pipe = pipe

    def hset(self, key: str, field: str, value: Any) -> _RedisPipeline:
        self._pipe.hset(key, field, value)
        return self

    def hdel(self, key: str, *fields: str) -> _RedisPipeline:
        self._pipe.hdel(key, *fields)
        return self

    def set(self, key: str, value: Any) -> _RedisPipeline:
        self._pipe.set(key, value)
        return self

    def sadd(self, key: str, *members: Any) -> _RedisPipeline:
        self._pipe.sadd(key, *members)
        return self

    def setex(self, key: str, ttl: int, value: Any) -> _RedisPipeline:
        self._pipe.setex(key, ttl, value)
        return self

    def zadd(self, key: str, mapping: dict[Any, float]) -> _RedisPipeline:
        self._pipe.zadd(key, mapping)
        return self

    def expire(self, key: str, ttl: int) -> _RedisPipeline:
        self._pipe.expire(key, ttl)
        return self

    def execute(self) -> list[Any]:
        try:
            return list(self._pipe.execute())
        except Exception:
            return []


_cache_instance: CacheBackend | None = None


def get_cache() -> CacheBackend:
    """Return the process-wide cache backend (in-memory by default).

    When ``RAG_REDIS_URL`` is set and the ``redis`` package is importable,
    returns a :class:`RedisCache`; otherwise a :class:`LocalCache`. The
    result is cached per process. Never returns ``None`` — the ported
    modules' ``if cache is None`` guards simply never trigger locally.
    """
    global _cache_instance
    if _cache_instance is None:
        url = (settings.redis_url or "").strip()
        backend: CacheBackend
        if url:
            try:
                import redis  # noqa: F401 - optional dependency probe

                backend = RedisCache(url)
            except Exception:
                backend = LocalCache()
        else:
            backend = LocalCache()
        _cache_instance = backend
    return _cache_instance


def _reset_cache_for_tests() -> None:
    """Test helper: drop the cached backend so a fresh one is created."""
    global _cache_instance
    _cache_instance = None
