"""Import guard — transparent stubs for optional ``app.*`` integrations.

The standalone rugbrain container never ships the rmi-backend ``app``
package, but many ``rugbrain`` modules reference ``app.*`` helpers for
optional integration (semantic cache, ANN index, knowledge graph,
entity extraction, cross-encoder reranker, data feeders, …). Historically
those imports raised ``ModuleNotFoundError``, which silently degraded
features or crashed endpoints that did not guard the import.

This module installs stub modules into ``sys.modules`` for every known
``app.*`` name referenced by rugbrain, so ``from app.x import y`` always
resolves. Missing modules become transparent stubs that log a warning
and return safe defaults (empty dicts / falsy values); call sites then
degrade gracefully instead of raising.

Install FIRST in the entry point (``rugbrain.main``)::

    from rugbrain.import_guard import install_import_guard

    install_import_guard()

If the real ``app`` package IS importable (e.g. rugbrain running inside
rmi-backend), the guard leaves it untouched and the real modules are
used instead of stubs.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import types
from collections.abc import Generator, Iterator
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

# Known ``app.*`` modules referenced by rugbrain. Kept in sync with
# `grep -rhoE "from (app\.[a-zA-Z0-9_.]+) import" src/rugbrain/`.
_APP_MODULES: list[str] = [
    "app.ann_index",
    "app.confidence",
    "app.core.redis",
    "app.cross_chain",
    "app.cross_encoder_reranker",
    "app.deployer_reputation",
    "app.domains.databus.news_intel",
    "app.domains.databus.news_provider",
    "app.domains.databus.rag_engine",
    "app.domains.databus.social_intel",
    "app.domains.databus.x_intel",
    "app.domains.scanners.unified_scanner",
    "app.entity_extraction",
    "app.hallucination_guard",
    "app.investigation_narratives",
    "app.knowledge_graph",
    "app.mmr_dedup",
    "app.query_transform",
    "app.rag.embeddings",
    "app.rag.incremental_indexer",
    "app.rag.parent_retriever",
    "app.rag_agentic",
    "app.rate_limiter",
    "app.scam_sources",
    "app.semantic_cache",
]


class _StubCallable(dict[Any, Any]):
    """Falsy stub that is callable, awaitable, iterable and dict-like.

    Any attribute access / call / await / index returns another stub, so
    call sites can chain arbitrarily without raising. Being a ``dict``
    subclass keeps FastAPI/JSON encoding working (it serializes as ``{}``).
    """

    _warned: ClassVar[set[str]] = set()

    def __init__(self, label: str = "") -> None:
        super().__init__()
        self._label = label

    def _log(self) -> None:
        if self._label and self._label not in self._warned:
            self._warned.add(self._label)
            logger.warning(
                "rugbrain import_guard: %s unavailable — using stub (feature degraded)",
                self._label,
            )

    def __call__(self, *args: object, **kwargs: object) -> _StubCallable:
        self._log()
        return _StubCallable(self._label)

    def __await__(self) -> Generator[Any, None, _StubCallable]:
        async def _empty() -> _StubCallable:
            self._log()
            return _StubCallable(self._label)

        return _empty().__await__()

    def __iter__(self) -> Iterator[_StubCallable]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __getattr__(self, name: str) -> _StubCallable:
        self._log()
        return _StubCallable(f"{self._label}.{name}" if self._label else name)

    def __getitem__(self, key: object) -> _StubCallable:
        return _StubCallable(self._label)

    def __bool__(self) -> bool:
        return False


class _StubModule(types.ModuleType):
    """Namespace-style stub package: any attribute access yields a stub."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.__path__: list[str] = []
        # Give the stub a real __file__ so stdlib ``inspect.getfile`` /
        # torch's custom-op registration can resolve it. Without this,
        # loading a sentence-transformers model inside the container raises
        # ``TypeError: <module 'app.X' ...> is a built-in module``.
        self.__file__ = os.path.abspath(__file__)

    def __getattr__(self, name: str) -> _StubCallable:
        return _StubCallable(f"{self.__name__}.{name}")


class _SemanticCacheStub:
    """Semantic-cache stub: reports NO cache hit and no-ops stores.

    ``check()`` must return ``None``: call sites branch on
    ``cached is not None`` and would otherwise treat the stub as a
    cache hit and skip the real vector search.
    """

    async def check(self, query_vec: list[float]) -> None:
        return None

    async def store(self, query_vec: list[float], results: list[object]) -> None:
        return None


def _build_semantic_cache_module() -> types.ModuleType:
    mod = _StubModule("app.semantic_cache")
    mod.get_semantic_cache = lambda: _SemanticCacheStub()  # type: ignore[attr-defined]
    mod.SemanticCache = _SemanticCacheStub  # type: ignore[attr-defined]
    return mod


_TAILORED: dict[str, types.ModuleType] = {
    "app.semantic_cache": _build_semantic_cache_module(),
}


def _ensure_stub(name: str) -> None:
    """Inject a stub module for ``name`` (and warn once)."""
    if name in sys.modules:
        return
    mod = _TAILORED.get(name) or _StubModule(name)
    sys.modules[name] = mod
    logger.warning("rugbrain import_guard: %s not found — installed stub", name)


def install_import_guard() -> int:
    """Ensure every known ``app.*`` module resolves (real or stub).

    Returns the number of stub modules installed. Idempotent: real
    modules already importable are left untouched.
    """
    installed = 0
    for dotted in _APP_MODULES:
        parts = dotted.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if parent not in sys.modules:
                try:
                    importlib.import_module(parent)
                except ImportError:
                    _ensure_stub(parent)
                    installed += 1
        if dotted in sys.modules:
            continue
        try:
            importlib.import_module(dotted)
        except ImportError:
            _ensure_stub(dotted)
            installed += 1
    return installed
