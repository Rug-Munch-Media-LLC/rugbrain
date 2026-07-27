# RMI RAG — Architecture

**Status:** canonical
**Last updated:** 2026-07-27
**Lines of code:** ~15K Python (53 modules)

---

## Overview

RMI RAG is a hybrid retrieval-augmented generation system purpose-built for
crypto intelligence. It provides semantic + sparse + entity-graph search over
token analysis, wallet profiles, scam patterns, forensic reports, market
intel, and contract audits.

The system runs as a FastAPI service (default) with a separate one-shot ingest
pipeline. Both modes share the same Qdrant vector store and Redis cache.

---

## Component Diagram

```
┌───────────────────────────────────────────────────────────────┐
│                    rmi-rag FastAPI Service                      │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌─────────┐  │
│  │ /health  │  │  /ready      │  │ /api/v1/*  │  │ /docs   │  │
│  │ liveness │  │  readiness   │  │ RAG routes │  │ OpenAPI │  │
│  └──────────┘  └──────────────┘  └──────┬─────┘  └─────────┘  │
│                                         │                      │
│  ┌──────────────────────────────────────┴──────────────────────┐│
│  │                    Search Service Layer                      ││
│  │  three_pillar_search()  search_multi_collection()           ││
│  │  search_similar()       search_with_transform()             ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
         │                        │                  │
    ┌────▼────┐           ┌───────▼───────┐    ┌─────▼─────┐
    │  Qdrant │           │    Redis      │    │  Langfuse │
    │  Vector │◄──────────│ cache+metrics │    │  Tracing  │
    │  Store  │           └───────────────┘    └───────────┘
    └────▲────┘
         │
    ┌────▼────┐
    │ Ingest  │
    │ Pipeline│
    │(one-shot)│
    └─────────┘
```

---

## Data Flow

```
                        INGEST PIPELINE (one-shot)
  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐
  │ GoPlus API   │───►│ embedder     │───►│ Qdrant        │
  │ PhishTank    │    │ (BGE-M3)     │    │ threat_intel  │
  │ Community    │    │              │    │ collection    │
  └──────────────┘    └──────────────┘    └───────────────┘

                       QUERY FLOW (API)
  ┌──────┐    ┌──────────────────┐    ┌──────────────────┐
  │Client│───►│ three_pillar_    │───►│ RRF Fusion       │
  │      │    │ search()         │    │ (Reciprocal Rank)│
  └──────┘    │                  │    └────────┬─────────┘
              │ Pillar 1: Dense  │             │
              │   (Qdrant ANN)   │    ┌────────▼─────────┐
              │ Pillar 2: Sparse │    │ MMR Dedup        │
              │   (BM25+SPLADE)  │    │ (Diversity)      │
              │ Pillar 3: Entity │    └────────┬─────────┘
              │   (KG + lookup)  │             │
              └──────────────────┘    ┌────────▼─────────┐
                                      │ Cross-encoder    │
                                      │ Rerank (opt)     │
                                      └────────┬─────────┘
                                               │
                                      ┌────────▼─────────┐
                                      │ Parent-child     │
                                      │ Context Expand   │
                                      └────────┬─────────┘
                                               │
                                      ┌────────▼─────────┐
                                      │ Response         │
                                      │ (results + pill- │
                                      │  ar attribution) │
                                      └──────────────────┘
```

---

## Module Map (53 modules)

### API Layer
| Module | Lines | Purpose |
|--------|-------|---------|
| `main.py` | ~130 | FastAPI app, lifespan, CORS, health/ready endpoints |
| `endpoints.py` | ~250 | APIRouter: confidence, chunking, deployer risk, cross-chain, image, investigation, watches, scam-check, email |
| `metrics_api.py` | — | Prometheus/metrics endpoint |

### Search & Retrieval
| Module | Lines | Purpose |
|--------|-------|---------|
| `search_service.py` | ~1130 | Core service: ingest, search_similar, three_pillar_search, stats, GoPlus analysis |
| `parent_retriever.py` | — | Parent document retrieval (chunk expansion) |
| `rrf_fusion.py` | — | Reciprocal Rank Fusion across pillar results |
| `search_similar.py` | — | Similarity search helpers |
| `sparse_search.py` | — | BM25 sparse text search |
| `splade_bm25.py` | — | SPLADE + BM25 hybrid sparse search |

### Vector Store
| Module | Lines | Purpose |
|--------|-------|---------|
| `qdrant_store.py` | ~270 | Qdrant REST wrapper: search, hybrid_search, insert, collection management with connection fallback |
| `bundle_cluster.py` | — | Document bundling/clustering for Qdrant |

### Embeddings
| Module | Lines | Purpose |
|--------|-------|---------|
| `embeddings.py` | — | Public interface: get_embedding, get_embeddings |
| `embedder.py` | — | Base embedder abstraction |
| `crypto_embeddings.py` | — | Crypto-specific embedding heads: tokens, wallets, scams, contracts |
| `bge_embedders.py` | — | BGE-M3 embedder config |
| `embed_utils.py` | — | Embedding utility functions |
| `embed_tiers.py` | — | Tiered embedding (fast/accurate) |
| `embedding_router.py` | — | Route queries to optimal embedding model |
| `embedding_finetune.py` | — | Fine-tuning for embedding models |
| `citations.py` | — | Citation-aware embeddings |

### Chunking
| Module | Lines | Purpose |
|--------|-------|---------|
| `chunking.py` | — | Core text chunking |
| `app_chunking.py` | — | Document chunking for ingest |
| `contextual_chunking.py` | — | Parent-child chunking |
| `solidity_chunker.py` | — | Solidity source code AST chunking |
| `chunking_config.yaml` | — | Chunk size/overlap config |

### Knowledge Graph & Entity
| Module | Lines | Purpose |
|--------|-------|---------|
| `graph_rag.py` | — | Knowledge Graph RAG expansion |
| `cross_chain.py` | — | Cross-chain entity resolution |
| `feature_extractors.py` | — | Feature extraction from entities |

### Query Processing
| Module | Lines | Purpose |
|--------|-------|---------|
| `query_classifier.py` | — | Classify query type (entity/keyword/semantic) |
| `query_rewriter.py` | — | Rewrite queries for better retrieval |
| `cross_encoder.py` | — | Cross-encoder reranker (bge-m3) |

### Scam Detection
| Module | Lines | Purpose |
|--------|-------|---------|
| `scam_detection.py` | — | Scam pattern detection logic |
| `seed_scams.py` | — | Seed known scam patterns into Qdrant |
| `pattern_extractor.py` | — | Extract scam patterns from text |

### Streaming & Real-time
| Module | Lines | Purpose |
|--------|-------|---------|
| `streaming.py` | — | Watch-based streaming alerts |
| `realtime_alerts.py` | — | Real-time alert dispatch |
| `firehose.py` | — | Event firehose for market data |
| `feedback.py` | — | User feedback collection |

### Evaluation
| Module | Lines | Purpose |
|--------|-------|---------|
| `evaluation.py` | — | RAG evaluation harness |
| `eval_ragas.py` | — | RAGAS metric computation |

### Multimodal
| Module | Lines | Purpose |
|--------|-------|---------|
| `multimodal.py` | — | Image analysis, logo theft detection |

### Observability
| Module | Lines | Purpose |
|--------|-------|---------|
| `observability.py` | ~200 | Langfuse tracing + local Redis metrics fallback |
| `analytics_storage.py` | — | Analytics data storage |

### Data Sources & Indexing
| Module | Lines | Purpose |
|--------|-------|---------|
| `rag_ingestion.py` | ~250 | Nightly indexing pipeline (3 AM UTC) |
| `incremental_indexer.py` | — | Delta-based incremental indexer |
| `github_feeder.py` | — | GitHub issues/PRs → RAG |
| `historical.py` | — | Historical data backfill |
| `legacy_supabase.py` | — | Supabase migration support |

### Agentic / Tool
| Module | Lines | Purpose |
|--------|-------|---------|
| `agentic.py` | — | Agentic retrieval orchestration |
| `tool.py` | — | CLI tool entry point |
| `rag_provider.py` | — | DataBus provider integration |
| `permanence.py` | — | R2 permanence for long-term storage |

### Python Package
| Module | Purpose |
|--------|---------|
| `__init__.py` | Public API surface re-exports |
| `__main__.py` | CLI entry point (`python -m rmi_rag`) |

---

## Fleet Dependencies

| Service | Host (Docker) | Port | Purpose |
|---------|--------------|------|---------|
| Qdrant | `rmi-qdrant` | 6333 | Vector database (Cosine distance, 1024d) |
| Redis | `rmi-redis` | 6379 | Cache, metrics, rate limiting |
| Langfuse | `rmi-langfuse` | 3000 | LLM observability (optional) |
| OTel Collector | `rmi-otel-collector` | 4317 | OpenTelemetry export (optional) |

### Connection Fallback

The Qdrant client probes URLs in order:
1. `QDRANT_URL` env var (default: `http://rmi-qdrant:6333`)
2. `http://127.0.0.1:{QDRANT_PORT}` (local dev)
3. `http://localhost:{QDRANT_PORT}` (local dev)

The first responsive URL is cached for the process lifetime. If none respond,
the primary URL is used and callers handle connection errors gracefully.

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness probe (probes Qdrant + Redis) |
| GET | `/ready` | Readiness probe (app started) |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc UI |
| GET | `/openapi.json` | OpenAPI 3.1 spec |
| POST | `/api/v1/rag/confidence` | Confidence scoring |
| POST | `/api/v1/rag/chunk-solidity` | Solidity AST chunking |
| POST | `/api/v1/scanner/deployer-risk` | Deployer risk scoring |
| POST | `/api/v1/rag/cross-chain` | Cross-chain identity resolution |
| POST | `/api/v1/rag/image-analysis` | Image analysis (multimodal) |
| POST | `/api/v1/rag/logo-check` | Logo theft detection |
| POST | `/api/v1/rag/investigate` | Investigation narrative generation |
| GET | `/api/v1/rag/graph-communities` | Knowledge graph communities |
| POST | `/api/v1/rag/watch` | Create streaming watch |
| GET | `/api/v1/rag/watches` | List active watches |
| GET | `/api/v1/email/accounts` | Email account list |
| GET | `/api/v1/rag/v2/scam-check` | Scam check (rate-limited, free/pro tiers) |

---

## Deployment

### Docker Compose (production)
```bash
docker compose up -d rag-api          # Start API server
docker compose run --rm rag-ingest    # Run one-shot ingest
```

### Local dev
```bash
pip install -e ".[dev]"
python -m rmi_rag                     # Start dev server on :8012
python -m rmi_rag --ingest            # Run ingest
make api-dev                          # Same, via Makefile
```

### Port
The API binds to **8012** (allocated per PORTS.md §8001-8009 range for rmi-* sub-products).
