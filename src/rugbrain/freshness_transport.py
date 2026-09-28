"""RugBrain — read-only Qdrant transport for the freshness probe.

Every call here is a GET or a scroll POST. Nothing writes, upserts, deletes,
re-indexes, or triggers an ingest: refreshing a stale feed would destroy the
evidence the freshness report exists to produce.

The payload flattening and timestamp parsing live here because both are pure
and both are the low-level layer everything else is built on.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final

DEFAULT_QDRANT_URL: Final = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")

#: Epoch bounds for numeric stamps. The floor is 2001-09-09, the ceiling is one
#: day past the 2038 rollover. Anything outside is not a timestamp: a bare
#: integer year, a duration, or a count mistaken for a clock.
PLAUSIBLE_EPOCH_FLOOR: Final = 1_000_000_000.0
PLAUSIBLE_EPOCH_CEIL: Final = 2_173_894_400.0
#: How far past "now" a string stamp may sit and still be called ingest time.
#: Measured 2026-09-28: `rag_documents` and `defi_exploits` both carry content
#: dates of 2026-12-08, eleven days in the future. A content date read as
#: ingest time reports a dead feed as fresh, so anything beyond this tolerance
#: is refused rather than believed.
FUTURE_TOLERANCE_DAYS: Final = 2


class QdrantError(RuntimeError):
    """Qdrant was unreachable or answered something unusable."""


def _fetch(path: str, url: str, body: dict | None, timeout: float) -> dict:
    """One HTTP round trip. Raises QdrantError; never bare-excepts.

    The scheme is checked because the URL comes from an environment variable:
    only http/https are opened, so a mis-set ``QDRANT_URL`` cannot make this
    read a local file.
    """
    if not url.startswith(("http://", "https://")):
        raise QdrantError(f"refusing non-http qdrant url scheme: {url!r}")
    req = urllib.request.Request(  # noqa: S310 — scheme guarded above; local Qdrant host
        url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — scheme guarded
            return json.load(resp)
    except urllib.error.URLError as exc:
        raise QdrantError(f"unreachable at {path}: {exc.reason}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise QdrantError(f"non-JSON for {path}") from exc


def list_collections(url: str = DEFAULT_QDRANT_URL, timeout: float = 25.0) -> list[str]:
    """Names live in Qdrant. GET ``/collections`` only, no payload scanned.

    Note this build answers names only — ``points_count`` lives per collection,
    so callers wanting mass must fetch each one. Measured 2026-09-28: 96 names.
    """
    raw = _fetch("/collections", url, None, timeout).get("result", {}).get("collections", [])
    return sorted(str(c["name"]) for c in raw if isinstance(c, dict) and c.get("name"))


def collection_info(name: str, url: str = DEFAULT_QDRANT_URL, timeout: float = 25.0) -> dict:
    """``points``/``status`` for one collection. GET only."""
    result = _fetch(f"/collections/{name}", url, None, timeout).get("result", {})
    if not isinstance(result, dict) or "points_count" not in result:
        raise QdrantError(f"no points_count for {name}")
    return {"points": int(result["points_count"]), "status": str(result.get("status", "unknown"))}


def iter_points(
    name: str,
    url: str = DEFAULT_QDRANT_URL,
    *,
    cap: int = 200,
    page: int = 50,
    timeout: float = 40.0,
) -> Iterator[dict]:
    """Yield up to ``cap`` points by scroll. Read-only; ``with_vector`` false."""
    offset: object = None
    seen = 0
    while seen < cap:
        body: dict = {"limit": min(page, cap - seen), "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = _fetch(f"/collections/{name}/points/scroll", url, body, timeout).get("result", {})
        points = result.get("points") or []
        for point in points:
            if isinstance(point, dict):
                yield point
        seen += len(points)
        offset = result.get("next_page_offset")
        if not points or offset is None:
            return


def payload_leaves(node: object, prefix: str = "") -> dict[str, object]:
    """Flatten a payload to dotted-path leaves. Lists are sampled, not walked."""
    if isinstance(node, dict):
        out: dict[str, object] = {}
        for key, val in node.items():
            out.update(payload_leaves(val, prefix + key + "."))
        return out
    if isinstance(node, list):
        out = {}
        for item in node[:5]:
            out.update(payload_leaves(item, prefix))
        return out
    return {prefix.rstrip("."): node}


def parse_timestamp(value: object) -> datetime | None:
    """Parse a scalar into an aware UTC datetime, or None if unusable.

    Rejects bools, None, implausible epochs, and stamps further ahead of now
    than :data:`FUTURE_TOLERANCE_DAYS` — a content date mislabelled as ingest
    is refused, not believed.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        secs = float(value)
        if secs > 1e12:
            secs /= 1000.0
        if not PLAUSIBLE_EPOCH_FLOOR <= secs <= PLAUSIBLE_EPOCH_CEIL:
            return None
        try:
            return datetime.fromtimestamp(secs, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or len(value.strip()) < 8:
        return None
    text = value.strip().replace("Z", "+00:00")
    horizon = datetime.now(UTC) + timedelta(days=FUTURE_TOLERANCE_DAYS)
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            got = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except (ValueError, TypeError):
            continue
        if got.tzinfo is None:
            got = got.replace(tzinfo=UTC)
        return None if got > horizon else got
    return None
