"""Fetching one source, or all of them, with failures made visible.

One job — turn the registry into documents, and never let a transport failure
look like a healthy empty source.

``fetch_source`` still returns ``list[IngestDoc]``: the contract does not
change. A failed fetch is no longer indistinguishable from a healthy empty
source, though: it is logged with a ``reason=`` token and re-raised, so the
caller MUST wrap this in ``try/except`` (or use :func:`fetch_source_result`) to
report it. The previous behaviour — swallow the failure and hand back a bare
``[]`` the caller then counted as a successful zero — is what let 15+ sources
die silently behind a green exit.

Fetching is deliberately scaffolding-grade: sources do one tree-listing call
plus per-file raw fetches (bounded by ``limit``), never multi-GB clones.
Chunking/embedding/storage stay in the existing ingest pipelines — use
``to_crawl_document`` to bridge into ``ingest_common.store_documents``.
"""

from __future__ import annotations

import logging

from rugbrain.ingest_sources import registry
from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.fetch_state import clear_fetch_error, take_fetch_error
from rugbrain.ingest_sources.outcome import FetchFailure, SourceResult

log = logging.getLogger(__name__)


def _resolve_source(name: str) -> registry.Source | None:
    """Resolve a source by name through the package facade.

    Looked up on the facade at call time rather than imported by value: the
    package is the documented entry point, and tests patch
    ``rugbrain.ingest_sources.get_source`` to substitute a stub source. A
    by-value import captured here would silently ignore that patch and fetch
    the real registry instead of the stub.

    This function is deliberately *not* named ``get_source``. The facade's
    ``get_source`` is the registry's lookup; if this module also exported that
    name, the facade binding would point back here and the lookup below would
    recurse into itself.
    """
    from rugbrain import ingest_sources

    return ingest_sources.get_source(name)


async def fetch_source(name: str, limit: int | None = None) -> list[IngestDoc]:
    """Fetch *name*; ``[]`` when unknown, or on a failure that is *recorded*.

    Still returns ``list[IngestDoc]`` — the contract does not change. A failed
    fetch is no longer indistinguishable from a healthy empty source, though:
    it is logged with a ``reason=`` token and re-raised, so the caller MUST
    wrap this in ``try/except`` (or use :func:`fetch_source_result`) to report
    it. The previous behaviour — swallow the failure and hand back a bare
    ``[]`` the caller then counted as a successful zero — is what let 15+
    sources die silently behind a green exit.
    """
    src = _resolve_source(name)
    if src is None:
        log.warning("unknown_source name=%s available=%s", name, registry.list_sources())
        return []
    clear_fetch_error()
    docs = await src.fetch(limit=limit)
    err = take_fetch_error()
    if err is not None:
        raise FetchFailure(err, name=name, docs=docs)
    return docs


async def fetch_source_result(name: str, limit: int | None = None) -> SourceResult:
    """Outcome-bearing fetch: never raises, reports failure explicitly."""
    try:
        return SourceResult(name=name, docs=await fetch_source(name, limit), error=None)
    except FetchFailure as exc:
        return SourceResult(name=exc.name, docs=exc.docs, error=exc.reason)


async def fetch_all(limit: int | None = None) -> dict[str, list[IngestDoc]]:
    """Fetch every source; returns ``{source_name: [IngestDoc, ...]}``.

    A failing source is re-raised as :class:`FetchFailure` *after* logging, so
    a batch run cannot be mistaken for a full pass. Use
    :func:`fetch_all_results` to collect outcomes without aborting.
    """
    out: dict[str, list[IngestDoc]] = {}
    for src in registry.SOURCES:
        out[src.name] = await fetch_source(src.name, limit)
        log.info("source_done name=%s docs=%d", src.name, len(out[src.name]))
    return out


async def fetch_all_results(limit: int | None = None) -> list[SourceResult]:
    """Fetch every source, one failure never aborts the batch."""
    results: list[SourceResult] = []
    for src in registry.SOURCES:
        result = await fetch_source_result(src.name, limit)
        results.append(result)
        state = "ok" if result.error is None else f"ERROR {result.error.as_log_line()}"
        log.info("source_done name=%s docs=%d %s", result.name, len(result.docs), state)
    return results


__all__ = [
    "fetch_all",
    "fetch_all_results",
    "fetch_source",
    "fetch_source_result",
]
