"""Observability — optional OpenInference/OTLP tracing (off by default)."""

from __future__ import annotations

from rugbrain.observability.tracing import is_phoenix_enabled, setup_phoenix, trace_span

__all__ = ["is_phoenix_enabled", "setup_phoenix", "trace_span"]
