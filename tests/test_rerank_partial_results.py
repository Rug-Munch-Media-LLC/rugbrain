"""SiliconFlow returns a *subset* of documents — that is a valid body, not a failure.

2026-10-22 live: 13 documents -> 8 `results` entries. The provider omits chunks
it cannot tokenize and stops at its per-request budget, so the old
`len(results) == len(documents)` check rejected a healthy 200 and every query
fell through to the 401/breaker rungs (45-80s instead of ~3s).

The shape check therefore pins the DOCUMENTED minimum only: `results[]` whose
entries carry an in-range integer `index` and a numeric `relevance_score`.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugbrain import rerank_lanes


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict[str, Any]:
        return self._payload


CALLS: list[dict[str, Any]] = []


class _FakeClient:
    """Records posted payloads and answers with one canned response."""

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self._status = status

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(
        self, url: str, headers: dict | None = None, json: dict | None = None
    ) -> _FakeResponse:
        CALLS.append({"url": url, "payload": json or {}})
        return _FakeResponse(self._payload, self._status)


@pytest.fixture(autouse=True)
def _siliconflow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only SiliconFlow has a key, and no rung is breaker-open."""
    for provider in rerank_lanes._PROVIDERS:
        _, _, env_name, _ = rerank_lanes._PROVIDERS[provider]
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setattr(rerank_lanes, "_KEYS", {"siliconflow": "test-key"})
    monkeypatch.setattr(rerank_lanes.provider_breaker, "is_dead", lambda _name: False)
    monkeypatch.setattr(rerank_lanes.provider_breaker, "mark_dead", lambda *_a, **_k: None)
    CALLS.clear()


async def test_partial_results_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """8 of 13 entries scored -> usable ranking, no fallback hop."""
    docs = [f"chunk {i} reentrancy withdraw" for i in range(13)]
    payload = {
        "results": [
            {"index": i, "relevance_score": 0.9 - i / 100} for i in (0, 3, 5, 7, 9, 10, 11, 12)
        ]
    }
    monkeypatch.setattr(rerank_lanes.httpx, "AsyncClient", lambda **_kw: _FakeClient(payload))

    out = await rerank_lanes.hosted_rerank("reentrancy", docs)

    assert out is not None, "a partial body must not be treated as malformed"
    assert len(out) == 8
    assert [r["index"] for r in out] == [0, 3, 5, 7, 9, 10, 11, 12]
    assert out[0]["relevance_score"] > out[-1]["relevance_score"]
    assert len(CALLS) == 1, "must not fall through to another rung"


async def test_out_of_range_index_is_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body that cannot be aligned to the sent documents stays a failure."""
    docs = ["a", "b"]
    payload = {"results": [{"index": 7, "relevance_score": 0.5}]}
    monkeypatch.setattr(rerank_lanes.httpx, "AsyncClient", lambda **_kw: _FakeClient(payload))

    assert await rerank_lanes.hosted_rerank("q", docs) is None


async def test_empty_results_is_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero scored documents means the rung answered nothing — fail fast."""
    payload = {"results": [], "id": "x"}
    monkeypatch.setattr(rerank_lanes.httpx, "AsyncClient", lambda **_kw: _FakeClient(payload))

    assert await rerank_lanes.hosted_rerank("q", ["a", "b"]) is None
