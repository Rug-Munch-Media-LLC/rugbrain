"""Qdrant transport for the 2560 -> 4096 twin fill.

Everything that talks to Qdrant lives here so the planning logic in
``plan.py`` stays pure and testable, and so the shape knowledge — unnamed
vs named vector, dense-only vs sparse+dense — is in exactly one place.

Qdrant :6334 is PRODUCTION. Nothing in this module deletes; it reads shapes
and point counts, and writes upserts only when the caller passes --execute.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

import httpx

logger = logging.getLogger("eliminate_4b.qdrant")

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
SCROLL_PAGE = 512


class Abort(RuntimeError):
    """A refusal or a broken contract. Stop; never guess past one."""


def result(c: httpx.Client, method: str, path: str, **kw: Any) -> Any:
    r = c.request(method, f"{QDRANT_URL}{path}", **kw)
    if r.status_code == 404:
        raise Abort(f"404 from Qdrant: {path}")
    r.raise_for_status()
    return r.json()["result"]


def collection_info(c: httpx.Client, name: str) -> dict[str, Any] | None:
    """None when the collection does not exist (404), else its info."""
    r = c.get(f"{QDRANT_URL}/collections/{name}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    info: dict[str, Any] = r.json()["result"]
    return info


def vector_shape(info: dict[str, Any]) -> tuple[int, str | None]:
    """Return (size, vector_name); name is None for the unnamed default.

    The twins are NOT uniform, which is the whole reason this is read rather
    than assumed. Measured 2026-09-29 on :6334:
      * education_8b          -> unnamed single 4096 vector
      * codebase_reference_v2_8b -> NAMED "dense" 4096 PLUS a "sparse" vector
    Qdrant serialises the unnamed default as {"": {...}}. Treating that ""
    key as a name would make every unnamed twin look named and send every
    upsert down a field the collection does not have.
    """
    v = info["config"]["params"].get("vectors")
    if v is None:
        return 0, None
    if isinstance(v, dict) and "size" in v:
        return int(v["size"]), None
    named = {k: s for k, s in v.items() if k}
    if not named:
        return int(next(iter(v.values()))["size"]), None
    name, spec = next(iter(named.items()))
    return int(spec["size"]), name


def has_sparse(info: dict[str, Any]) -> bool:
    return bool(info["config"]["params"].get("sparse_vectors"))


def create_twin(
    c: httpx.Client, name: str, *, size: int, vector_name: str | None = None
) -> dict[str, Any]:
    """Create an empty 4096 twin that mirrors the base's vector SHAPE.

    The shape is not cosmetic. Qdrant serialises the unnamed default as
    ``{"": {...}}``, so a twin created without ``vector_name`` is unnamed and
    an upsert addressed to a name is rejected; created WITH a name when the
    base has none, every point written lands on a field the collection lacks
    and the twin stays empty. Hence the name is a REQUIRED argument, derived
    from the base by the caller, not defaulted here.

    This is the only write in the module that is not an upsert. It is reached
    solely from ``plan(execute=True)`` for a target marked ``create_twin``, and
    it creates an empty collection — it cannot destroy or alter an existing
    one, because Qdrant rejects a create on a name that already exists.
    """
    vectors: dict[str, Any] = {(vector_name or ""): {"size": size, "distance": "Cosine"}}
    return result(
        c,
        "PUT",
        f"/collections/{name}",
        json={"vectors": vectors},
    )


def scroll(
    c: httpx.Client, name: str, *, with_payload: bool = False, with_vector: bool = False
) -> Iterator[list[dict[str, Any]]]:
    """Stream a collection page by page.

    A generator, not a list: the 2.1M-point base must never be materialised
    whole, and the twin's id set is built incrementally from these pages.
    """
    offset = None
    while True:
        body: dict[str, Any] = {
            "limit": SCROLL_PAGE,
            "with_payload": with_payload,
            "with_vector": with_vector,
        }
        if offset is not None:
            body["offset"] = offset
        res = result(c, "POST", f"/collections/{name}/points/scroll", json=body)
        pages = res["points"]
        if not pages:
            return
        yield pages
        offset = res.get("next_page_offset")
        if offset is None:
            return


def twin_id_set(c: httpx.Client, twin: str) -> set[str]:
    return {str(p["id"]) for page in scroll(c, twin) for p in page}


def count(c: httpx.Client, name: str) -> int:
    info = collection_info(c, name)
    return int(info["points_count"]) if info else 0


def upsert(c: httpx.Client, twin: str, points: list[dict[str, Any]]) -> None:
    """wait=true so the next count reflects what was just written."""
    result(c, "PUT", f"/collections/{twin}/points?wait=true", json={"points": points})
