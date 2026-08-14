"""Phoenix (Arize) observability for the crypto RAG pipeline.

Sends OpenTelemetry spans to an OpenInference-compatible collector
(e.g. Arize Phoenix) covering
every RAG stage:

  - embedding  — Infinity ``/embeddings``
  - retrieval  — Qdrant / in-process ANN search
  - reranking  — Infinity ``/rerank``
  - synthesis  — engine orchestration (``three_pillar_search``,
    ``ingest_document``)

Design notes:
  - Guarded by ``PHOENIX_ENABLED`` (default off). Every helper is a no-op
    when disabled, so the backend never depends on Phoenix being up.
  - rmi-backend's RAG pipeline is custom ``httpx``-based — there is no
    LangChain / LangGraph / OpenAI-SDK call path to auto-instrument, so we
    trace manually with :func:`trace_span` instead of framework
    instrumentors.
  - Only an OpenInference-compatible OTLP SDK (e.g. ``arize-phoenix-otel``,
    ``phoenix.otel.register``) is required at runtime; the UI package stays
    out of the image.
  - OpenInference semantic attributes (``openinference.*``) are applied so
    spans render correctly in the Phoenix UI (span kind, model, counts).

Usage:
    with trace_span("rag.embedding.infinity",
                    {"openinference.span.kind": "EMBEDDING"}):
        vectors = await embedder.embed(texts)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from rugbrain._logging import get_logger

log = get_logger(__name__)

# Module-level defaults — resolved from env at setup_phoenix() call time
# so runtime config (e.g. tests) can override after import.
_DEFAULT_PROJECT: str = "rugbrain"
_DEFAULT_ENDPOINT: str = ""  # env-only: set OTEL_EXPORTER_OTLP_ENDPOINT / PHOENIX_ENDPOINT; off by default

# Module-level state — set once by setup_phoenix().
_enabled: bool = False
_setup_attempted: bool = False


class _NoopSpan:
    """Dropped-in placeholder so callers can always call span helpers."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        pass

    def end(self) -> None:
        pass


_NOOP_SPAN = _NoopSpan()


def is_phoenix_enabled() -> bool:
    """Whether Phoenix tracing is active in this process."""
    return _enabled


def setup_phoenix() -> bool:
    """Register the Phoenix OTLP exporter and enable RAG tracing.

    Safe to call multiple times — only the first successful call
    registers. Returns ``True`` when tracing is active. Never raises: on
    any failure we log and continue with tracing disabled (fail-open).
    """
    global _enabled, _setup_attempted
    if _enabled or _setup_attempted:
        return _enabled
    _setup_attempted = True

    if os.getenv("PHOENIX_ENABLED", "false").lower() not in ("true", "1", "yes", "on"):
        log.info("phoenix_tracing_disabled")
        return False

    try:
        from phoenix.otel import register  # type: ignore[import-not-found]

        project = os.getenv("PHOENIX_PROJECT", _DEFAULT_PROJECT)
        endpoint = os.getenv("PHOENIX_ENDPOINT", _DEFAULT_ENDPOINT)
        register(project_name=project, endpoint=endpoint)
        _enabled = True
        log.info("phoenix_tracing_enabled endpoint=%s project=%s", endpoint, project)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("phoenix_tracing_setup_failed err=%s", exc)
        _enabled = False
    return _enabled


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Open a named OTEL span under the RAG tracer (no-op when disabled).

    The yielded object is the live OTEL span (or a no-op placeholder when
    Phoenix is disabled), so callers can attach extra attributes:

        with trace_span("rag.retrieval.hybrid") as span:
            hits = await retriever.retrieve(query)
            span.set_attribute("retrieval.result_count", len(hits))

    Span duration is recorded automatically by the OTEL SDK; latency
    breakdown by component is derived from child spans in the Phoenix UI.
    """
    if not _enabled:
        yield _NOOP_SPAN
        return

    try:
        from opentelemetry import trace as otel_trace  # type: ignore[import-not-found]

        tracer = otel_trace.get_tracer("rugbrain", "0.1.0")
        with tracer.start_as_current_span(name) as span:
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
            yield span
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("trace_span_failed name=%s err=%s", name, exc)
        yield _NOOP_SPAN


__all__ = [
    "is_phoenix_enabled",
    "setup_phoenix",
    "trace_span",
]
