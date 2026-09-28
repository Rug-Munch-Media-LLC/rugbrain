"""Failure and outcome types for a source fetch.

One job — make a dead source impossible to report as a successful zero.

A zero-document source is NOT automatically healthy: HTTP failures are
recorded on the :mod:`fetch_state` channel with an operator reason token, and
the fetch layer turns that into either a raised :class:`FetchFailure` or an
explicit :class:`SourceResult` carrying ``error``. See ``fetch_state`` for the
protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.fetch_state import FetchError


class FetchFailure(Exception):
    """A source fetch hit an HTTP/transport failure (reason + partial docs).

    Raised by ``fetch_source`` so a failure can never be reported as a
    successful zero. ``reason`` is the operator token (``github_not_found``,
    ``github_rate_limited``, ``network_timeout``, …) and ``docs`` keeps any
    partial result so a caller can still store what did arrive.
    """

    def __init__(self, error: FetchError, *, name: str, docs: list[IngestDoc]) -> None:
        super().__init__(f"source_fetch_failed name={name} {error.as_log_line()}")
        self.reason = error
        self.name = name
        self.docs = docs


@dataclass(frozen=True)
class SourceResult:
    """Outcome of one source fetch: documents + the failure reason, if any.

    ``error is None`` means a clean run — including a legitimately empty
    source. ``error`` carries the reason token that makes a dead source
    visible instead of a silent zero.
    """

    name: str
    docs: list[IngestDoc] = field(default_factory=list)
    error: FetchError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def count(self) -> int:
        return len(self.docs)


__all__ = ["FetchFailure", "SourceResult"]
