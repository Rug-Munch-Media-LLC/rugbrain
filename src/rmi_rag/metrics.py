"""Prometheus metrics for the RAG API.

Exposes a minimal, dependency-light set of counters/histograms/gauges
so the /metrics endpoint can be scraped by the fleet Prometheus.

No external dependencies beyond ``prometheus_client``.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# ── Search ──────────────────────────────────────────────────────────
search_requests = Counter(
    "rag_search_total",
    "Total search requests",
    ["collection"],
)
search_latency = Histogram(
    "rag_search_seconds",
    "Search latency",
    ["collection"],
)

# ── Embedding ───────────────────────────────────────────────────────
embed_requests = Counter(
    "rag_embed_total",
    "Total embedding requests",
)
embed_latency = Histogram(
    "rag_embed_seconds",
    "Embedding latency",
)

# ── Ingest ──────────────────────────────────────────────────────────
ingest_total = Counter(
    "rag_ingest_total",
    "Total documents ingested",
    ["collection"],
)

# ── Collection / health state ───────────────────────────────────────
collection_size = Gauge(
    "rag_collection_points",
    "Points per collection",
    ["collection"],
)
qdrant_healthy = Gauge(
    "rag_qdrant_healthy",
    "Qdrant health status",
)
ollama_healthy = Gauge(
    "rag_ollama_healthy",
    "Ollama health status",
)


def latest() -> bytes:
    """Return the Prometheus exposition format payload."""
    return generate_latest()
