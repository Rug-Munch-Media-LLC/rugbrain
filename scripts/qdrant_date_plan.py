"""Qdrant REST client + payload-index plan for the crypto backfill.

Split out of ``backfill_payload_date.py`` to keep both modules under the firm's
200-line hard cap. **Read-only surface here except ``create_index``**, which the
caller must gate behind an explicit ``--apply``.

The ``INDEX_PLAN`` is the documented recommendation: a ``datetime`` index is what
makes a date range filter cheap, a ``keyword`` index is what makes an exact
match cheap. Both collections in this repo currently carry an **empty**
``payload_schema`` — i.e. every filter today is a full payload scan.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

QDRANT = "http://127.0.0.1:6334"

# Ordered by trust: a dedicated exploit date beats a generic published date.
DATE_SOURCES: dict[str, list[str]] = {
    "defi_exploits": ["date", "date_ts", "metadata.date", "metadata.first_pool_activity"],
    "news_v2": ["metadata.published", "metadata.published_at", "metadata.ts"],
    "threat_intel_v2": ["metadata.published", "metadata.ts", "metadata.date", "captured_at"],
    "scam_intel_v2": ["metadata.reported_date", "metadata.date"],
    "known_scams": ["metadata.date", "metadata.first_seen"],
    "scam_patterns": ["metadata.date", "metadata.created"],
    "crypto_vulnerabilities": ["metadata.date_added", "metadata.published"],
    "contract_audits": ["metadata.audit_date", "metadata.date"],
    "regulation_enforcement": ["metadata.date", "metadata.release_date"],
    "education": ["metadata.published", "metadata.fetched_at"],
    "wallet_v2": ["metadata.scanned_at", "metadata.first_seen", "metadata.last_seen"],
}

# Collections with no date-bearing source at all — a backfill would be a no-op
# and an index would sit empty. Listed so the gap is explicit, not accidental.
NO_DATE_SOURCE = (
    "news_articles",
    "hacks_v2",
    "security_feeds",
    "social_intel",
    "scanned_tokens",
    "exchange_flows",
    "liquidation_events",
)

_DATETIME = {"type": "datetime"}
_KEYWORD = {"type": "keyword"}


def _plan(*fields: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"field": name, "params": spec} for name, spec in fields]


INDEX_PLAN: dict[str, list[dict[str, Any]]] = {
    # date buys: date>=X && date<=Y as a range scan, not a payload walk.
    # chain buys: exact-match "exploits on base", case-normalised.
    # severity buys: cheap triage filter alongside the other two.
    "defi_exploits": _plan(("date", _DATETIME), ("chain", _KEYWORD), ("severity", _KEYWORD)),
    "news_v2": _plan(("date", _DATETIME), ("chain", _KEYWORD)),
    "threat_intel_v2": _plan(("date", _DATETIME), ("chain", _KEYWORD), ("severity", _KEYWORD)),
    "scam_intel_v2": _plan(("date", _DATETIME), ("chain", _KEYWORD)),
    "crypto_vulnerabilities": _plan(("date", _DATETIME)),
    "contract_audits": _plan(("date", _DATETIME)),
    "regulation_enforcement": _plan(("date", _DATETIME)),
    "education": _plan(("date", _DATETIME)),
    "wallet_v2": _plan(("date", _DATETIME)),
    # No date source exists here — chain index only, and say so rather than
    # creating a `date` index that can never match a point.
    "known_scams": _plan(("chain", _KEYWORD)),
    "scam_patterns": _plan(("chain", _KEYWORD)),
}


def request(method: str, path: str, body: Any = None, timeout: int = 60) -> Any:
    """Call the Qdrant REST API. Raises RuntimeError with the Qdrant error text."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 — fixed loopback host, not user input
        QDRANT + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            loaded = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode(errors="replace")
        raise RuntimeError(f"qdrant {method} {path} -> {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"qdrant unreachable at {QDRANT}: {exc.reason}") from exc
    return loaded.get("result")


def points_count(collection: str) -> Any:
    info = request("GET", f"/collections/{collection}") or {}
    return info.get("points_count", "?")


def existing_indexes(collection: str) -> dict[str, Any]:
    info = request("GET", f"/collections/{collection}") or {}
    return info.get("payload_schema") or {}


def create_index(collection: str, field: str, spec: dict[str, Any]) -> None:
    request(
        "PUT",
        f"/collections/{collection}/index",
        {"field_name": field, "field_schema": spec},
        timeout=180,
    )
