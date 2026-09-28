"""Minimal Qdrant REST client for the ``rug_events`` build.

One job: read source points out of the collections the corpus already lives in
and upsert the projected rug events. Written against the REST API for the same
reason ``scripts/apply_payload_indexes.py`` is — it is a build script, not a
service, and a dependency-free path cannot fail on a client version skew.

Every call is READ against the canonical collections. The only write is
``upsert_points`` into ``rug_events`` (or a scratch name in tests), which is
the collection this module exists to populate.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:6334"

#: Namespace for deterministic event_id -> UUIDv5. Fixed, so re-running the
#: build produces the SAME point ids and upsert stays idempotent (no duplicates).
#: Qdrant accepts only unsigned-int or UUID point ids, and the natural key here
#: is a three-part string ("deflabx_solrpds:<token>:<year>"), so it is hashed
#: into a UUID. The original key is kept in the payload as ``event_id``, so
#: nothing is lost and the mapping stays auditable.
EVENT_NAMESPACE = uuid.UUID("6f2c1d84-3a5b-5c7e-9d10-2b8c4e6f0a31")


def point_id(event_id: str) -> str:
    """Deterministic UUIDv5 point id for a string event identity."""
    return str(uuid.uuid5(EVENT_NAMESPACE, str(event_id)))


class QdrantError(RuntimeError):
    """A Qdrant call failed; carries the Qdrant error text."""


def request(
    base: str,
    method: str,
    path: str,
    body: Any = None,
    timeout: int = 300,
) -> Any:
    """Call the Qdrant REST API and return the ``result`` field."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 — caller-supplied Qdrant host, not user input
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            loaded = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise QdrantError(f"qdrant {method} {path} -> {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise QdrantError(f"qdrant unreachable at {base}: {exc.reason}") from exc
    return loaded.get("result")


def scroll(
    base: str,
    collection: str,
    limit: int = 2000,
    offset: Any = None,
    query_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Scroll one page of points with payloads, no vectors."""
    body: dict[str, Any] = {"limit": limit, "with_payload": True, "with_vector": False}
    if offset is not None:
        body["offset"] = offset
    if query_filter:
        body["filter"] = query_filter
    return request(base, "POST", f"/collections/{collection}/points/scroll", body)["points"]


def scroll_all(
    base: str,
    collection: str,
    page: int = 2000,
    max_points: int = 200_000,
    query_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Scroll an entire collection, paging until it is exhausted."""
    out: list[dict[str, Any]] = []
    offset: Any = None
    while len(out) < max_points:
        points = scroll(
            base,
            collection,
            limit=min(page, max_points - len(out)),
            offset=offset,
            query_filter=query_filter,
        )
        if not points:
            break
        out.extend(points)
        offset = points[-1]["id"]
        if len(points) < page:
            break
    return out


def get_text(url: str, timeout: int = 60) -> str:
    """GET *url* and return the body, or ``""`` when unavailable (fail-open).

    Static CSV/JSON dataset URLs for the structured rug sources. A flaky
    upstream must degrade the corpus, never abort the build.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        log.warning("fetch_failed url=%s err=%s", url, str(exc)[:160])
        return ""
    return body.decode("utf-8", errors="replace")


def collection_info(base: str, collection: str) -> dict[str, Any]:
    """Return the collection's ``result`` (status, points_count, indexes)."""
    return request(base, "GET", f"/collections/{collection}") or {}


def upsert_points(
    base: str,
    collection: str,
    points: list[dict[str, Any]],
    wait: bool = True,
) -> Any:
    """Upsert ``points`` (each ``{id, vector, payload}``) into *collection*."""
    return request(
        base,
        "PUT",
        f"/collections/{collection}/points",
        {"points": points, "wait": wait},
    )


__all__ = [
    "DEFAULT_URL",
    "EVENT_NAMESPACE",
    "QdrantError",
    "collection_info",
    "get_text",
    "point_id",
    "request",
    "scroll",
    "scroll_all",
    "upsert_points",
]
