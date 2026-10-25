"""Single source of truth for Polymarket citation URLs (``?ref=<slug>``).

rugbrain and rmi-content-pipeline both import from here — the base URL and the
ref slug are never duplicated as string literals in either repo.

Gate contract: a Polymarket citation is valid only when it carries
``?ref=<POLYMARKET_REF_SLUG>`` (default ``rugmunch``) and, when a market slug is
supplied, names that market.
"""

from __future__ import annotations

import os
import re

BASE_URL = "https://polymarket.com"
REF_ENV = "POLYMARKET_REF_SLUG"
DEFAULT_REF_SLUG = "rugmunch"
_REF_RE = re.compile(r"[?&]ref=([A-Za-z0-9_.-]+)")


def ref_slug() -> str:
    """The ref slug, from ``POLYMARKET_REF_SLUG`` (fallback: rugmunch)."""
    return (os.getenv(REF_ENV) or DEFAULT_REF_SLUG).strip() or DEFAULT_REF_SLUG


def ref_url(market_slug: str, base: str = BASE_URL) -> str:
    """Build ``<base>/market/<slug>?ref=<slug>`` — the only URL builder."""
    slug = (market_slug or "").strip().strip("/")
    if not slug:
        raise ValueError("market_slug is required for a Polymarket citation")
    sep = "&" if "?" in base else "?"
    return f"{base.rstrip('/')}/market/{slug}{sep}ref={ref_slug()}"


def citation_ok(text: str, market_slug: str | None = None) -> bool:
    """True when ``text`` carries a valid referred citation (pipeline gate)."""
    match = _REF_RE.search(text or "")
    if not match or match.group(1) != ref_slug():
        return False
    if market_slug is not None:
        return market_slug.strip() in (text or "")
    return True
