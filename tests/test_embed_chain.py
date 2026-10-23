"""Fallback chain + fail-loud contract for the corpus embed lane.

Hermetic: fake HTTP clients only. Each test pins the invariant that broke on
2026-10-23 — a failed embed must never look like a successful sweep, and a
non-4096-dim lane (the 0.6B/1024-d :8004 lane) must never be selected.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import embed_chain
import embed_lane

DIM = 4096


def _run(coro):
    """Run one coroutine without relying on pytest-asyncio mode config."""
    return asyncio.run(coro)


def _vec(dim: int = DIM) -> list[float]:
    return [0.01] * dim


class _Resp:
    def __init__(self, status: int = 200, vectors: list[list[float]] | None = None, text: str = "") -> None:
        self.status_code = status
        self._vectors = vectors or []
        self.text = text

    def json(self) -> dict:
        return {"data": [{"index": i, "embedding": v} for i, v in enumerate(self._vectors)]}


class _Client:
    """Fake httpx client: one queued response per call, records URLs."""

    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []

    async def post(self, url: str, **kwargs: object) -> _Resp:
        self.urls.append(url)
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _lane_env(monkeypatch, tmp_path):
    monkeypatch.setattr(embed_chain, "FAILURE_FILE", str(tmp_path / "failures.jsonl"))
    monkeypatch.setenv("LLAMA_EMBED_KEY", "local-key")
    monkeypatch.setenv("QWEN_EMBED_URL", "http://127.0.0.1:8024/v1")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "deepinfra-key")
    monkeypatch.setenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
    monkeypatch.setenv("RUG_EMBED_FAILURE_FILE", str(tmp_path / "failures.jsonl"))
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("RUG_EMBED_STRICT", raising=False)
    embed_chain.reset_failures()
    embed_chain._cooldown.clear()


def test_chain_is_local_first_then_hosted():
    assert [lane.name for lane in embed_chain.build_chain()] == ["local", "deepinfra"]


def test_chain_prefers_embedded_provider_but_keeps_fallbacks():
    names = [lane.name for lane in embed_chain.build_chain("deepinfra")]
    assert names[0] == "deepinfra"
    assert "local" in names


def test_1024_lane_is_refused_outright(monkeypatch):
    monkeypatch.setenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1")
    monkeypatch.delenv("DEEPINFRA_API_KEY")
    with pytest.raises(embed_chain.EmbedLaneExhausted):
        embed_chain.build_chain()


def test_dimension_violation_falls_through_to_next_lane():
    client = _Client([_Resp(vectors=[_vec(1024)]), _Resp(vectors=[_vec(DIM)])])
    vectors = _run(embed_lane.embed_batch(["a"], client=client))
    assert vectors and len(vectors[0]) == DIM
    assert client.urls[1].startswith("https://api.deepinfra.com")
    assert embed_chain.failure_count() == 1


def test_every_lane_failing_raises_and_counts(tmp_path):
    client = _Client([_Resp(500, text="boom"), _Resp(402, text="overdue invoices")])
    with pytest.raises(embed_lane.EmbedLaneExhausted):
        _run(embed_lane.embed_batch(["a"], client=client))
    assert embed_chain.failure_count() >= 2
    assert "402" in (tmp_path / "failures.jsonl").read_text()


def test_402_parks_lane_so_the_next_batch_skips_it():
    client = _Client([_Resp(402, text="overdue invoices"), _Resp(vectors=[_vec(DIM)])])
    assert _run(embed_lane.embed_batch(["a"], client=client))
    client2 = _Client([_Resp(vectors=[_vec(DIM)])])
    _run(embed_lane.embed_batch(["b"], client=client2))
    assert client2.urls == ["https://api.deepinfra.com/v1/embeddings"]


def test_strict_off_is_an_escape_hatch_not_a_default():
    client = _Client([_Resp(500), _Resp(500)])
    assert _run(embed_lane.embed_batch(["a"], client=client, raise_on_failure=False)) == [None]
