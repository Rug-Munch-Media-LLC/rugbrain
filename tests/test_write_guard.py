"""A retired 2560-d slot must refuse writes even when the widths agree."""

import asyncio
from types import SimpleNamespace

from rugbrain.qdrant_store.write_guard import slot_accepts


class _Client:
    """REST double (writefix46): the write path speaks Qdrant REST via httpx."""

    def __init__(self, size: int) -> None:
        self._size = size

    async def get(self, _path: str):
        config = {"params": {"vectors": {"size": self._size, "distance": "Cosine"}}}
        return SimpleNamespace(status_code=200, json=lambda: {"result": {"config": config}})

    async def put(self, *_a, **_k):  # contract: the guard must never write
        raise AssertionError("write_guard issued a write")

    async def post(self, *_a, **_k):
        raise AssertionError("write_guard issued a write")


def test_retired_2560_slot_refused():
    assert asyncio.run(slot_accepts(_Client(2560), "scan_intel", 2560)) is False


def test_canonical_4096_slot_accepted():
    assert asyncio.run(slot_accepts(_Client(4096), "scan_intel_8b", 4096)) is True


def test_foreign_width_refused():
    assert asyncio.run(slot_accepts(_Client(4096), "c", 2560)) is False
