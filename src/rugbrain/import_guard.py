"""Import guard — compatibility shim (no ``app.*`` stubs remaining).

The standalone rugbrain container never ships the rmi-backend ``app``
package. Historically this module injected 24 ``app.*`` stub modules into
``sys.modules`` so optional ``from app.x import y`` integrations degraded
gracefully.

**Standalone sever (2026-09-18, docs/MODERNIZATION.md §5):** all 24 entries
were proven unused — ``grep -rnE "^\\s*(from app[.\\s]|import app\\b)"
src/rugbrain/`` returns nothing. They are deleted from ``_APP_MODULES``.
The guard mechanism is retained (a no-op when the list is empty) until
Phase 8 removes this module and its ``install_import_guard()`` call.

[REVIEW] Phase 8: delete this file + the ``install_import_guard()`` call in
``main.py`` (out of scope for the sever task — main.py is not owned here).
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

# Previously 24 ``app.*`` stub modules. All provably unused as of the
# standalone sever (grep proof in the module docstring). Shrink-only:
# do NOT re-add an entry without a live import + a regression test.
_APP_MODULES: list[str] = []


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


_COMPAT_MAP: dict[str, str] = {
    "app.core.redis": "get_redis_async,get_redis",
    "app.confidence": "score_confidence",
    "app.entity_extraction": "EntityLookup,extract_entities",
    "app.ann_index": "get_ann_index,AnnIndex",
    "app.semantic_cache": "get_semantic_cache,SemanticCache",
    "app.mmr_dedup": "mmr_dedup_results",
    "app.knowledge_graph": "get_knowledge_graph",
    "app.query_transform": "transform_query",
    "app.scam_sources": "GoPlusConnector",
    "app.cross_encoder_reranker": "get_reranker",
    "app.rag.parent_retriever": "get_parent_retriever",
}


def _try_compat(name: str) -> types.ModuleType | None:
    """Try to load a compat module for *name* from rugbrain.compat.

    Returns a synthetic sys.modules entry that re-exports the compat
    names under the *name* namespace, or None if no compat exists.
    """
    target = _COMPAT_MAP.get(name)
    if not target:
        return None
    try:
        from rugbrain import compat

        mod = types.ModuleType(name)
        for attr in target.split(","):
            attr = attr.strip()
            if hasattr(compat, attr):
                setattr(mod, attr, getattr(compat, attr))
        sys.modules[name] = mod
        logger.debug("rugbrain import_guard: %s → rugbrain.compat.%s", name, target)
        return mod
    except Exception as exc:
        logger.debug("rugbrain import_guard: compat failed for %s: %s", name, exc)
        return None


def _ensure_stub(name: str) -> None:
    """Inject a stub module for ``name`` (and warn once).

    First tries rugbrain.compat; if no compat exists, creates a
    safe stub that logs a warning on first use.
    """
    if name in sys.modules:
        return
    # Try rugbrain-native compat first
    if _try_compat(name):
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
