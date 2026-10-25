"""TCP reachability probe for a retrieval layer, cached for 60 s."""

from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

TTL_SECONDS = 60.0
_CACHE: dict[str, tuple[float, bool, int]] = {}


def clear_cache() -> None:
    _CACHE.clear()


def probe_layer(url: str, timeout: float = 1.5) -> dict[str, object]:
    """True if the layer's host:port accepts a connection. Never raises."""
    now = time.monotonic()
    cached = _CACHE.get(url)
    if cached and now - cached[0] < TTL_SECONDS:
        return {"reachable": cached[1], "cached": True, "latency_ms": cached[2]}
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            reachable = True
    except OSError:
        reachable = False
    latency = int((time.monotonic() - started) * 1000)
    _CACHE[url] = (now, reachable, latency)
    return {"reachable": reachable, "cached": False, "latency_ms": latency}
