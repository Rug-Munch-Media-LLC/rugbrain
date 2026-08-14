# RugBrain Migration Guide

This document maps the private monorepo's RAG stack (`app/rag/`) to the
standalone `rugbrain` package and records the extraction order. It is the
implementation companion to the checklist in the private repo
(`docs/RUGBRAIN-EXTRACTION-CHECKLIST.md`).

> ⚠️ **Internal-only references in this file** (paths like `app/rag/...`)
> are extraction bookkeeping, **not** shipped methodology. Delete this
> file (or replace with a public CHANGELOG) before any public release.

## Source map

| rugbrain module | Private source | Status |
|---|---|---|
| `src/rugbrain/core/models.py` | `app/rag/models.py` | stub |
| `src/rugbrain/core/chunking.py` | `app/rag/chunking.py` | stub |
| `src/rugbrain/core/dedup.py` | `app/rag/dedup.py` | stub |
| `src/rugbrain/core/quality.py` | `app/rag/quality.py` | stub |
| `src/rugbrain/core/retrieval.py` | `app/rag/retrieval.py` | stub |
| `src/rugbrain/core/rerank.py` | `app/rag/rerank.py` | stub |
| `src/rugbrain/core/engine.py` | `app/rag/engine.py` | stub |
| `src/rugbrain/core/evaluation.py` | `app/rag/evaluation.py` | stub |
| `src/rugbrain/core/feedback.py` | `app/rag/feedback.py` | stub |
| `src/rugbrain/core/ratings.py` | `app/rag/ratings.py` | stub |
| `src/rugbrain/core/service.py` | `app/rag/service.py` | stub |
| `src/rugbrain/ingest/ingestion.py` | `app/rag/ingestion.py` | stub |
| `src/rugbrain/ingest/ingestion_models.py` | `app/rag/ingestion_models.py` | stub |
| `src/rugbrain/ingest/processor.py` | `app/rag/processor.py` | stub |
| `src/rugbrain/ingest/rate_limiter.py` | `app/rag/rate_limiter.py` | stub |
| `src/rugbrain/ingest/crawler.py` | `app/rag/crawler.py` | stub |
| `src/rugbrain/ingest/_crawler_constants.py` | `app/rag/_crawler_constants.py` | stub |
| `src/rugbrain/ingest/_crawler_helpers.py` | `app/rag/_crawler_helpers.py` | stub |
| `src/rugbrain/storage/qdrant_store.py` | `app/rag/qdrant_store.py` | stub |
| `src/rugbrain/storage/ann_index.py` | `app/rag/ann_index.py` | stub |
| `src/rugbrain/embed/embeddings.py` | `app/rag/embeddings.py` | stub |
| `src/rugbrain/embed/embedder.py` | `app/rag/embedder.py` | stub |
| `src/rugbrain/observability/tracing.py` | `app/rag/tracing.py` | stub |
| `src/rugbrain/settings.py` | `app/rag/config.py` + settings reads | **ported** (env-driven) |
| `src/rugbrain/providers.py` | boundary protocols (new) | **ported** |
| `src/rugbrain/api/` | private chat/rag routers (P2) | stub |

### Private-coupled modules — NOT ported

| Module | Reason | OSS treatment |
|---|---|---|
| `app/rag/tools.py` | imports private scanner + balances services | `ScannerClient` / `BalancesClient` protocols + stubs |
| `app/rag/graph.py` | uses private LLM config + knowledge graph | optional `KGProvider` / `LLMRouter`, default None |
| `app/rag/pryscraper_ingest.py` | bridges private pryscraper service | `CrawlerProvider` stub (no-op) |
| `app/rag/intelligence.py` | scanner-result semantic memory | stays private (v1) |
| `app/rag/scan_memory.py` | scan_v1 payload contract | stays private (v1) |
| `app/rag/compat.py` | re-exports private `app.crypto_embeddings` | leave behind |
| `app/rag/ingest_crypto_knowledge.py` | **contains shared-secret auth header** | never ships |

## Extraction order

1. **Scaffold** (done): pyproject, package layout, protocols, settings,
   example configs, this doc.
2. **Pure core** (one commit per module): models → chunking/dedup/quality/
   processor/rate_limiter/ingestion → evaluation/feedback/ratings.
3. **Portable core**: settings swap in engine/qdrant_store/crawler/
   embeddings/embedder/rerank; `KGProvider` seam in retrieval; tracing
   endpoint made env-only.
4. **Private-coupled**: providers + stubs shipped; tools/graph/pryscraper
   ported behind protocols or deferred.
5. **Tests/CI/shims**: port tests, shim `app.rag` re-exports, fleet files,
   tag `v0.1.0`.

## Pre-public-push scrub checklist

Before the first public tag, verify (this is a **gate**):

- [ ] `gitleaks detect --no-git` — zero findings
- [ ] `rg -n "rugmunch\.io|gopass|INTERNAL|X-RMI-Key|phoenix:" src/ README.md` — zero hits
- [ ] No `app.*` imports remain in `src/` (all replaced with protocols/settings)
- [ ] `config/*.json` contains only public/neutral URLs
- [ ] This `MIGRATION.md` replaced with a public CHANGELOG

## [REVIEW] open questions

- The existing `rmi-rag` repo already ships a `rugbrain` package name with
  a different codebase (`src/rmi_rag/` + a `sys.modules` alias). Decide
  supersede / merge / rename before the first public push.
- Confirm ship-vs-private for `ta_knowledge.py` (916 LOC) and
  `ratings.py` (628 LOC) — not in the original extraction list.
