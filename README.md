# rugbrain

The retrieval engine behind our crypto intelligence answers: it ingests alerts,
articles and market data, embeds them, and serves hybrid (vector + lexical)
retrieval with reranking to whatever is asking — API, agent, or dashboard.

**Status:** beta — ingest and retrieval run continuously, the HTTP surface still
changes between releases. **Licence:** MIT, see [LICENSE](LICENSE).

Canonical: https://git.rugmunch.io/RugMunchMedia/rugbrain

## Quickstart

Python 3.11+, Qdrant, Redis, Postgres.

```bash
git clone https://git.rugmunch.io/RugMunchMedia/rugbrain.git
cd rugbrain
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # QDRANT_URL, REDIS_PASSWORD, RMI_RAG_API_KEY
uvicorn rugbrain.main:app --reload --port 8012
```

Compose runs the whole loop (API, ingest worker, Presidio, dashboard) and
requires the Redis password in the environment:

```bash
export REDIS_PASSWORD='...'
docker compose up -d
```

Ingest is a separate process from serving. Nothing is retrievable until an ingest
run has happened; a fresh Qdrant collection answers with an empty result set, not
an error.

## What it does

- **Ingest.** Pulls from configured feeds (alerts, articles, market data), chunks,
  embeds, and writes to Qdrant with metadata that survives retrieval.
- **Hybrid retrieval.** Vector search plus a lexical arm, then a reranker, because
  a cosine hit alone is not good enough for entity and address lookups.
- **Grounded answers.** Answers cite the chunks they used; when the corpus has
  nothing, the service says so instead of improvising.
- **PII scrubbing.** Presidio runs as its own service in the compose stack so
  scraped text is cleaned before it is embedded.
- **Health and evals.** `health_routes.py` for liveness; `eval/` holds the
  retrieval evaluation set used before changing chunking or models.

## How it is put together

```
src/rugbrain/
  main.py  app/          FastAPI app, auth, health
  app_chunking/          chunkers per source type
  ingest                 feed pullers and pipeline
  retrieval              vector + lexical + rerank
  alerts/                alert ingestion and webhooks
```

Compose services: `rag-api`, `rag-ingest`, `presidio`, `dashboard`,
`model-gateway`.

## Configuration

| Variable | Purpose |
|---|---|
| `RMI_RAG_API_KEY` | caller credential; requests without it are rejected |
| `QDRANT_URL`, `QDRANT_API_KEY` | vector store |
| `REDIS_PASSWORD` | cache and queue; required by compose |
| `RAG_EMBED_MODEL`, `RAG_EMBED_DIM` | embedding model and its dimension — must match the collection |
| `QWEN_EMBED_URL`, `GATEWAY_EMBED_URL` | embedding lanes, local and gateway |
| `RERANKER_URL`, `RERANKER_TIMEOUT` | rerank lane and timeout |
| `RMI_BACKEND_URL` | upstream intelligence API for enrichment |
| `SEARXNG_URL` | web fallback for sources outside the corpus |
| `FEAR_GREED_URL`, `FEAR_INDEX_POLL_SECONDS` | market sentiment feed and poll interval |
| `LOG_LEVEL` | logging verbosity |

Changing `RAG_EMBED_DIM` against an existing collection silently returns garbage
neighbours. Recreate the collection when you change the model or dimension.

## Known limits

- Embedding dimension is fixed per collection. There is no automatic migration.
- Retrieval quality depends on ingest freshness; the API cannot tell you a source
  stopped being crawled.
- CPU reranking is slow. Expect seconds, not milliseconds, on a large candidate
  set.
- `README.STALE-CLONE.md` marks a fork that must not be used as the source of
  truth.

## Licence

MIT.
