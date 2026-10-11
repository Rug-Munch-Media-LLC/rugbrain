"""Search rerank core contracts (was 55% — lines 253-298 dark).

Offline: httpx stubbed. Contracts for _score_candidates:

* disabled reranker → ([], "none") without HTTP
* empty candidates → ([], "none")
* unhealthy (recent failure) → skip probe within 60s window
* valid response: scores mapped back to ORIGINAL indices (empty docs
  skipped in payload but keep 0.0 slots)
* non-200 → ([], "none") + health marked bad
* transport error → health marked bad, ([], "none")
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest

from rugbrain.search import rerank as rr


@pytest.fixture(autouse=True)
def _healthy(monkeypatch):
    monkeypatch.setattr(rr, "RERANKER_ENABLED", True)
    # Hermetic: force the local lane (hosted rung reads DEEPINFRA_API_KEY /
    # /srv/secrets — present on Talos, absent in CI) and clear global strain
    # state polluted by sibling tests (module-level defaultdict).
    _prev_hosted = os.environ.get("RERANK_HOSTED")
    os.environ["RERANK_HOSTED"] = "0"
    rr._strain_failures.clear()
    rr._health["ok"] = True
    rr._health["last_check"] = time.time()
    yield
    if _prev_hosted is None:
        os.environ.pop("RERANK_HOSTED", None)
    else:
        os.environ["RERANK_HOSTED"] = _prev_hosted
    rr._health["ok"] = False
    rr._health["last_check"] = 0.0
    rr._strain_failures.clear()


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


class FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, json=None):
        if self._exc:
            raise self._exc
        return self._resp


@pytest.mark.asyncio
async def test_disabled_short_circuits(monkeypatch):
    monkeypatch.setattr(rr, "RERANKER_ENABLED", False)
    called = {"n": 0}

    class Spy(FakeClient):
        async def post(self, url, json=None):
            called["n"] += 1
            return _resp({"results": []})

    monkeypatch.setattr(rr.httpx, "AsyncClient", lambda **k: Spy())
    scores, method = await rr._score_candidates("q", [{"content": "doc"}])
    assert scores == [] and method == "none"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_empty_candidates():
    scores, method = await rr._score_candidates("q", [])
    assert scores == [] and method == "none"


@pytest.mark.asyncio
async def test_scores_mapped_back_to_original_indices(monkeypatch):
    # doc 1 is empty after chunking → skipped in payload but keeps slot
    candidates = [
        {"content": "real doc zero"},
        {"content": ""},
        {"content": "real doc two"},
    ]
    resp = _resp(
        {
            "results": [
                {"index": 0, "relevance_score": 0.9},  # first VALID doc
                {"index": 1, "relevance_score": 0.4},  # second VALID doc
            ]
        }
    )
    monkeypatch.setattr(rr.httpx, "AsyncClient", lambda **k: FakeClient(resp))
    scores, method = await rr._score_candidates("rug", candidates)
    assert method == "qwen3-reranker-8b"
    assert len(scores) == 3
    assert scores[0] == pytest.approx(0.9)  # original index 0
    assert scores[1] == 0.0  # empty doc keeps neutral slot
    assert scores[2] == pytest.approx(0.4)  # original index 2


@pytest.mark.asyncio
async def test_non200_marks_unhealthy(monkeypatch):
    monkeypatch.setattr(rr.httpx, "AsyncClient", lambda **k: FakeClient(_resp({}, status=500)))
    scores, method = await rr._score_candidates("q", [{"content": "d"}])
    assert scores == [] and method == "none"
    # unified path records the failure via _post_rerank -> health bad
    assert rr._health["ok"] is False


@pytest.mark.asyncio
async def test_transport_error_marks_unhealthy(monkeypatch):
    import httpx

    monkeypatch.setattr(
        rr.httpx,
        "AsyncClient",
        lambda **k: FakeClient(exc=httpx.ConnectError("down")),
    )
    scores, method = await rr._score_candidates("q", [{"content": "d"}])
    assert scores == [] and method == "none"
    assert rr._health["ok"] is False


@pytest.mark.asyncio
async def test_recent_failure_skips_probe(monkeypatch):
    rr._health["ok"] = False
    rr._health["last_check"] = time.time()  # just failed
    called = {"n": 0}

    class Spy(FakeClient):
        async def post(self, url, json=None):
            called["n"] += 1
            return _resp({"results": []})

    monkeypatch.setattr(rr.httpx, "AsyncClient", lambda **k: Spy())
    scores, method = await rr._score_candidates("q", [{"content": "d"}])
    assert scores == [] and method == "none"
    assert called["n"] == 0  # probe suppressed within 60s window
