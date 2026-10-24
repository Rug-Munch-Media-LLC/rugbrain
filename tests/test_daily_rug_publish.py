"""Daily Rug publication: canonical URL, evidence gate, idempotent slug, route."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rugbrain import daily_rug_evidence
from rugbrain.content_style import load_style
from rugbrain.daily_rug_publish import publish_daily_rug
from rugbrain.daily_rug_store import FileStoryStore
from rugbrain.routes.daily_rug import router

FIXTURES = Path("tests/fixtures/content_style")
HITS: list[dict[str, Any]] = json.loads((FIXTURES / "evidence_bridge.json").read_text())
STYLE = load_style()
LIMIT = int(STYLE["length_budgets"]["telegram_hard_limit"])
CANONICAL = "https://rugmunch.io/story/"


async def _search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return HITS


async def _thin(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return HITS[:1]


@pytest.fixture
def store(tmp_path: Path) -> FileStoryStore:
    return FileStoryStore(tmp_path / "stories")


async def test_publish_returns_canonical_url_and_writes_the_story(
    store: FileStoryStore,
) -> None:
    out = await publish_daily_rug(
        "bridge key compromise drain", date="2026-10-24", search_fn=_search, store=store
    )
    assert out["status"] == "ok" and out["published"] is True
    assert out["url"] == f"{CANONICAL}{out['slug']}"
    assert out["canonical_url"] == out["url"]
    assert "/blog/" not in out["url"]
    assert out["revision"] == 1
    assert out["sources"] >= 2

    story = store.get(out["slug"])
    assert story is not None
    assert story["url"] == out["url"]
    assert story["title"] and story["dek"]
    assert story["sections"] and story["sources"]
    assert story["open_graph"]["og:url"] == out["url"]
    assert story["open_graph"]["og:type"] == "article"
    assert story["body_markdown"].startswith("# ")


async def test_thin_evidence_refuses_and_writes_nothing(store: FileStoryStore) -> None:
    out = await publish_daily_rug("bridge drain", search_fn=_thin, store=store)
    assert out["status"] == "insufficient_evidence"
    assert out["published"] is False
    assert "only 1 citable source" in out["reason"]
    assert list(store.root.glob("*.json")) == []


async def test_slug_collision_updates_in_place(store: FileStoryStore) -> None:
    first = await publish_daily_rug(
        "Bridge Keys Drained", date="2026-10-24", search_fn=_search, store=store
    )
    second = await publish_daily_rug(
        "bridge keys drained", date="2026-10-24", search_fn=_search, store=store
    )
    assert second["slug"] == first["slug"]
    assert second["url"] == first["url"]
    assert (first["revision"], second["revision"]) == (1, 2)
    assert len(list(store.root.glob("*.json"))) == 1
    assert store.get(first["slug"])["created_at"]


async def test_telegram_summary_links_canonical_url_under_limit(
    store: FileStoryStore,
) -> None:
    out = await publish_daily_rug(
        "bridge key compromise drain", date="2026-10-24", search_fn=_search, store=store
    )
    telegram = out["telegram"]
    assert telegram["url"] == out["url"]
    assert out["url"] in telegram["text"]
    assert telegram["chars"] == len(telegram["text"])
    assert telegram["chars"] < LIMIT == 4096
    assert telegram["under_limit"] is True


def test_route_publishes_then_serves_by_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUGBRAIN_STORY_DIR", str(tmp_path / "stories"))

    async def fake_search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return HITS

    monkeypatch.setattr(daily_rug_evidence, "_default_search", fake_search)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/content/daily-rug",
            json={"topic": "bridge key compromise drain", "date": "2026-10-24"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["published"] is True and body["url"].startswith(CANONICAL)

        served = client.get(f"/api/v1/content/daily-rug/{body['slug']}")
        assert served.status_code == 200
        assert served.json()["url"] == body["url"]
        assert client.get("/api/v1/content/daily-rug/no-such-story").status_code == 404
