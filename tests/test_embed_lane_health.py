"""Embed-lane health snapshot: lane classification + loud width failures.

Patches the chain attrs on ``rugbrain.embeddings`` directly (try/finally via
the fixture helper) so this new test adds nothing to the repo monkeypatch
ratchet, which is needling on main already.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

import rugbrain.embeddings as em
from rugbrain.embeddings import RAGUnavailableError
from rugbrain.embeddings_pkg import lane_health as lh


@contextmanager
def _patched(backend: str, fn: Callable[[str], Any]) -> Iterator[None]:
    """Swap the chain's display/lane attrs for one probe; always restore."""
    saved = (em.current_backend, em.get_embedding)
    em.current_backend = lambda: backend
    em.get_embedding = fn
    try:
        yield
    finally:
        em.current_backend, em.get_embedding = saved


async def _fake_ok(text: str) -> list[float]:
    """The happy local lane: a real 4096d vector."""
    assert text  # the chain raises on empty input; a fake must never see it
    return [0.5] * 4096


async def _run(backend: str, fn: Callable[[str], Any]) -> dict:
    with _patched(backend, fn):
        return await lh.embed_lane_snapshot()


async def test_local_lane_classified_and_4096() -> None:
    snap = await _run("int", _fake_ok)
    assert snap["embed_lane"] == "local"
    assert snap["dim"] == 4096
    assert snap["probe_ok"] is True


async def test_hosted_lane_classified() -> None:
    snap = await _run("deepinfra", _fake_ok)
    assert snap["embed_lane"] == "hosted"


async def test_empty_vector_fails_loudly() -> None:
    async def empty(text: str) -> list[float]:
        return []

    with pytest.raises(RuntimeError, match="width guard"):
        await _run("int", empty)


async def test_1024d_vector_fails_loudly() -> None:

    async def narrow(text: str) -> list[float]:

        return [0.5] * 1024  # the 0.6B lane leaking into the corpus chain

    with pytest.raises(RuntimeError, match="1024d"):
        await _run("int", narrow)


async def test_chain_down_propagates() -> None:

    async def down(text: str) -> list[float]:
        raise RAGUnavailableError("no embedding backend available")

    with pytest.raises(RAGUnavailableError):
        await _run("int", down)
