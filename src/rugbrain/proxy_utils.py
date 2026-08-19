"""Proxy utilities — wraps httpx with Webshare SOCKS5 proxy support.

Reads proxy config from env:
  PROXY_URL=socks5://user:pass@host:port
  PROXY_ENABLED=true/false (default: auto-detect if PROXY_URL is set)

Usage:
  from rugbrain.proxy_utils import get_proxy_client, ProxyClient

  async with get_proxy_client() as client:
      r = await client.get(url)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PROXY_URL = os.getenv("PROXY_URL", "")
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "auto").lower()


def _should_use_proxy() -> bool:
    """Determine if proxy should be used."""
    if PROXY_ENABLED == "true":
        return True
    if PROXY_ENABLED == "false":
        return False
    # auto: use proxy if URL is set
    return bool(PROXY_URL)


def get_proxy_client(**kwargs: Any) -> httpx.AsyncClient:
    """Get an httpx AsyncClient optionally routed through the Webshare proxy.

    Use as a context manager:
        async with get_proxy_client(timeout=15) as client:
            r = await client.get(url)

    Passes all kwargs through to httpx.AsyncClient.
    """
    if _should_use_proxy():
        logger.debug("proxy_enabled url=%s", PROXY_URL[:30])
        return httpx.AsyncClient(proxies=PROXY_URL, **kwargs)  # type: ignore[arg-type]
    return httpx.AsyncClient(**kwargs)
