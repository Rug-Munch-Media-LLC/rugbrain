"""Declarative source registry: one file plus one entry adds a source.

One job — map a source name to its family, fetch callable and target
collection, so a new source does not edit a central import catalogue. This
coexists with :mod:`rugbrain.ingest_sources.registry` (the ``Source`` ABC
catalogue) while families migrate; see ``docs/ADR-ingest-architecture.md``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

#: ``fetch(client, limit) -> docs``. The client comes from ``shared.http``.
FetchFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class SourceEntry:
    """One registered source: who it is, what it fetches, where it lands."""

    name: str
    family: str
    fetch: FetchFn
    doc_type: str = ""
    collection: str = ""


REGISTRY: dict[str, SourceEntry] = {}


def register(
    name: str,
    *,
    family: str,
    fetch: FetchFn | None = None,
    doc_type: str = "",
    collection: str = "",
) -> Any:
    """Register *name* (usable as a decorator when *fetch* is omitted)."""

    def bind(fn: FetchFn) -> FetchFn:
        if name in REGISTRY:
            raise ValueError(f"source already registered: {name}")
        REGISTRY[name] = SourceEntry(name, family, fn, doc_type, collection)
        return fn

    return bind(fetch) if fetch is not None else bind


def get(name: str) -> SourceEntry | None:
    """The entry for *name*, or ``None`` when it was never registered."""
    return REGISTRY.get(name)


def names() -> tuple[str, ...]:
    """Every registered source name, sorted."""
    return tuple(sorted(REGISTRY))


def by_family(family: str) -> tuple[SourceEntry, ...]:
    """Every entry in *family*, sorted by name."""
    return tuple(e for e in (REGISTRY[n] for n in names()) if e.family == family)


__all__ = ["REGISTRY", "FetchFn", "SourceEntry", "by_family", "get", "names", "register"]
