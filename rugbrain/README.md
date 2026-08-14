# RugBrain

RugBrain is a self-hostable, hybrid-retrieval RAG engine. It combines
dense vector search (bge-m3 embeddings served over an
[Infinity](https://github.com/michaelfeil/infinity)-compatible HTTP
endpoint) with a lightweight sparse/BM25-style signal and reciprocal-rank
fusion, so a small knowledge base gets solid retrieval without a big
infrastructure footprint.

It is designed to be **embedded as a library** (the `rugbrain` Python
package) or **run as a small FastAPI service** (optional `api` extra),
backed by [Qdrant](https://qdrant.tech) for storage.

> **Status:** scaffold — the package skeleton is in place; the engine
> modules are being ported. See [`MIGRATION.md`](MIGRATION.md).

## What it does

- **Hybrid search** — dense + sparse signals fused with RRF (k=60), with
  optional cross-encoder reranking.
- **Idempotent ingestion** — content is chunked, deduplicated (SHA-256),
  quality-gated, and upserted with stable ids.
- **Fail-open by design** — every external dependency (Qdrant, embedding
  backend, reranker) degrades to a documented fallback instead of raising.
- **Observable** — optional OpenInference spans; export endpoint is
  env-configured and off by default.

## Requirements

- Python 3.11+
- Qdrant (or the `qdrant-client[local]` in-memory mode for development)
- An Infinity-compatible embedding endpoint (or any provider the
  `EmbeddingProvider` protocol adapts)
- Redis is **optional** — an in-memory cache fallback is used when unset

## Quick start

```bash
pip install -e ".[dev]"

# Point at your services (all defaults are localhost)
export RAG_QDRANT_URL="http://127.0.0.1:6333"
export INFINITY_URL="http://127.0.0.1:8001"

rugbrain ingest --collection docs path/to/knowledge.md
rugbrain search --collection docs "your question"
```

See [`config/collections.example.json`](config/collections.example.json)
for the (neutral) collection payload schema.

## Configuration

All configuration is env-driven via pydantic-settings, prefixed with
`RAG_` (e.g. `RAG_QDRANT_URL`, `RAG_COLLECTIONS`,
`RAG_CONTEXT_MAX_TOKENS`, `RAG_HYBRID_ALPHA`). See
[`src/rugbrain/settings.py`](src/rugbrain/settings.py) for the full list.
Credentials and endpoints come from your environment or secrets manager —
never from the repository.

## Development

```bash
pip install -e ".[dev]"
ruff check src
mypy src
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
