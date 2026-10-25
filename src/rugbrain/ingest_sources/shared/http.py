"""The one place an ingest source gets an HTTP client.

One job — build the client every source shares: timeout, redirects, our
User-Agent, optional ``PROXY_URL``. Sources receive the client, they do not
construct one, and they do not import ``httpx`` to get one.
"""

from __future__ import annotations

import os
from typing import Any

USER_AGENT = "RMI-RagBrain/0.1 (+https://rugmunch.io; source ingestion)"
DEFAULT_TIMEOUT = 30.0


def proxy_url() -> str | None:
    """Optional HTTP(S) proxy from ``PROXY_URL`` (credentials stay in env)."""
    return os.getenv("PROXY_URL", "").strip() or None


def client(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    """A configured ``httpx.AsyncClient`` — the only construction site."""
    import httpx

    merged = {"User-Agent": USER_AGENT}
    merged.update(headers or {})
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=merged,
        proxy=proxy_url(),
        **kwargs,
    )


__all__ = ["DEFAULT_TIMEOUT", "USER_AGENT", "client", "proxy_url"]
