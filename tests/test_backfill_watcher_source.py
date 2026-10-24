"""Tests for the WatcherGuru source backfill — no live Qdrant.

The transport is an httpx.MockTransport standing in for the JSON HTTP path the
script actually uses (scroll -> set_payload). Because the mock sees every body
verbatim, the tests prove exactly what we must guarantee against production:
only non-canonical watcher-matched ids are rewritten, the write body sets a
single ``source`` key, vectors are never requested, and all other payload
fields survive untouched.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.backfill_watcher_source import (
    CANONICAL,
    COLLECTIONS,
    SUBSTR,
    is_canonical,
    relabel_value,
    rewrite,
    run,
    watcher_value,
)


# ---------------------------------------------------------------- pure matcher
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("t.me/watcherguru", True),
        ("T.ME/WatcherGuru", True),
        ("WatcherGuru", True),
        ("@WatcherGuru", True),
        ("tg:watcherguru", True),
        ("x.com/watcherguru", True),
        ("t.me/watcherguruchat", True),
        (None, False),
        ("", False),
        ("cointelegraph", False),
        ("t.me/Watcher_Guru", False),
    ],
)
def test_watcher_value(raw: str | None, expected: bool) -> None:
    assert watcher_value(raw) is expected


def test_canonical_values_do_not_relabel() -> None:
    """The canonical form itself is a matched watcher value but never rewritten."""
    assert SUBSTR == "watcherguru"
    assert watcher_value(CANONICAL)
    assert is_canonical(CANONICAL)
    assert is_canonical(" t.me/watcherguru ")
    assert not relabel_value(CANONICAL)
    for v in ("tg:watcherguru", "x.com/watcherguru", "t.me/WatcherGuru", "WatcherGuru"):
        assert relabel_value(v)


# -------------------------------------------------------------- mock transport
def _points(ids: list[str], sources: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": i, "payload": {"source": sources[i]}} for i in ids]


def _scroll_page(points: list[dict[str, Any]], offset: str | None) -> dict[str, Any]:
    return {"result": {"points": points, "next_page_offset": offset}, "status": "ok"}


def test_rewrite_sets_only_source_and_chunks() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/points/payload" in request.url.path
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"result": {"status": "ok"}, "status": "ok"})

    ids = [f"id{i}" for i in range(250)]
    client = httpx.AsyncClient(base_url="http://mock:6334", transport=httpx.MockTransport(handler))
    asyncio.run(
        rewrite(
            client,
            "news_v2_8b",
            ids,
            CANONICAL,
            apply=True,
        )
    )
    assert [len(chunk["points"]) for chunk in captured] == [100, 100, 50]
    for chunk in captured:
        assert chunk["payload"] == {"source": CANONICAL}


def test_rewrite_dry_run_never_posts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("dry run must not POST set_payload")

    asyncio.run(
        rewrite(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            "news_v2_8b",
            ["a", "b"],
            CANONICAL,
            apply=False,
        )
    )


def test_run_relabels_mixed_case_and_preserves_everything_else() -> None:
    payload: dict[str, dict[str, Any]] = {
        "id1": {
            "source": "tg:watcherguru",
            "metadata": {"source": "WatcherGuru"},
            "chain": "sol",
            "content": "x",
        },
        "id2": {"source": "t.me/WatcherGuru", "chunk_page": 3},
        "id3": {"source": "t.me/watcherchat", "no": "match"},
        "id4": {"source": CANONICAL, "already": "done"},
    }
    ids = list(payload)
    writes: list[dict[str, Any]] = []
    pages = [[(i, payload[i].copy()) for i in ids]]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/points/scroll"):
            page = pages.pop(0) if pages else []
            pts = [{"id": i, "payload": pl} for i, pl in page if "source" in pl]
            return httpx.Response(200, json=_scroll_page(pts, None))
        if request.url.path.endswith("/points/payload"):
            writes.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={"result": {"status": "ok"}, "status": "ok"})
        raise AssertionError(request.url.path)

    report = asyncio.run(
        run(
            "http://mock:6334",
            ("news_v2_8b",),
            CANONICAL,
            apply=True,
            transport=httpx.MockTransport(handler),
        )
    )
    col = report["collections"]["news_v2_8b"]
    assert col["watchers_before"] == 3
    assert col["rewritten"] == 2
    # id3 (watcherchat) and id4 (already canonical) must not be rewritten.
    assert set(writes[0]["points"]) == {"id1", "id2"}
    assert writes[0]["payload"] == {"source": CANONICAL}
    # A single scanned pass: everything else stays in the mock's own payload.
    assert col["sources_before"] == {"tg:watcherguru": 1, "t.me/WatcherGuru": 1, CANONICAL: 1}
    assert "sources_after" in col


def test_run_is_idempotent_in_memory() -> None:
    """A second pass over canonical data rewrites nothing."""
    ids = ["a", "b"]
    sources = {i: CANONICAL for i in ids}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/points/scroll"):
            return httpx.Response(200, json=_scroll_page(_points([i for i in ids], sources), None))
        if request.url.path.endswith("/points/payload"):
            raise AssertionError("idempotent pass must not write")
        raise AssertionError(request.url.path)

    report = asyncio.run(
        run(
            "http://mock:6334",
            COLLECTIONS[:1],
            CANONICAL,
            apply=True,
            transport=httpx.MockTransport(handler),
        )
    )
    col = report["collections"]["news_v2_8b"]
    assert col["watchers_before"] == 2
    assert col["rewritten"] == 0
