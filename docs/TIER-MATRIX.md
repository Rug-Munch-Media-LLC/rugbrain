> ⚠️ **ARCHIVED 2026-08-29 — 0.6B TIER RETIRED.** The qwen3-embedding-0.6B (:8007) and qwen3-reranker-0.6B (:8008) lanes are GONE. Everything below about the speed tier is historical. Current doctrine: ONE canonical embedder (qwen3-embedding-4b @ 2560, DeepInfra primary + local :8004 fallback) + qwen3-reranker-4b (:8003) for all rerank. Legacy 1024-dim collections are served by 4B MRL-truncate. Chat: qwen3-8b-instruct :8009. This doc is kept for historical reference only.

# RugBrain Tier Matrix — which model serves what (authoritative)

> 2026-08-25 · The four local models + cloud fallback, their jobs, and the
> hard space boundaries. Tests: `tests/test_embedder_tier_contracts.py`.

## The four local models

| Tier | Model | Port | Dim | SERVES | NEVER SERVES |
|------|-------|------|-----|--------|--------------|
| **DEEP** | qwen3-embedding-4B | :8004 | 2560 | ALL corpus ingestion; ALL queries against qwen collections; long-form analysis | near-dup hot loops |
| **DEEP** | qwen3-reranker-4B | :8003 | — | big-corpus rerank; fast-lane strain fallback | — |
| **SPEED** | qwen3-embedding-0.6B | :8007 | 1024 | near-dup detection, clustering, cache keys — BOTH sides internal | corpus search (cross-model = silent empty) |
| **SPEED** | qwen3-reranker-0.6B | :8008 | — | consumer/snappy answer reranking (score-only, dim-free) | — |

## Why rerankers are dim-free but embedders are not

Embedders map text INTO a vector space; two vectors are comparable only if
they come from the SAME model. Rerankers read (query, document) PAIRS and
emit a relevance score — no space membership required. Therefore:

- 0.6B reranker can score docs retrieved from the 2560 corpus ✓
- 0.6B embedder can NEVER query the 2560 corpus ✗ (Qdrant rejects dim;
  worse, same-dim different-space would silently return garbage)

## Cloud fallback dims (the trap this doc exists for)

| Source | Native dim | Space | Usable for mainline? |
|--------|-----------|-------|---------------------|
| OpenRouter nemotron-embed-1b | 2048 | nemotron | ❌ NEVER (MRL-resize across models = poison) |
| NVIDIA NIM embeds | model-dep | nemotron family | ❌ NEVER (same) |
| Gateway :8010 cloud serve | varies | varies | ❌ refused at the lane already |

`RAG_ALLOW_CROSS_MODEL_EMBED=1` re-enables OR/NIM lanes ONLY for building
nemotron-native shadow namespaces (none active post-flip). Default refusal
is the T04 doctrine: fail loud beats false confidence.

## Ladders

- **Embed (auto)**: int :8004 → gateway :8010 → FAIL LOUD.
  (OR/NIM rungs guarded off for mainline; shadow builds opt in.)
- **Rerank (live/consumer)**: 0.6B :8008 → strain → 4B :8003 → original order.
- **Rerank (deep)**: 4B :8003 only.

## Consumer-side products (speed tier) — current wiring

| Product | Embed | Rerank |
|---------|-------|--------|
| near-dup / dedup / cache keys | 0.6B both sides (:8007) | — |
| bot `get_news` / Daily Rug stories | 4B query (:8004, must match corpus) | `rerank_tier="live"` → 0.6B (:8008) |
| federated search `lane=fast` | 4B query | 0.6B → 4B ladder |
| federated search `lane=deep` | 4B query | 4B only |

Consumer SPEED comes from the rerank lane + recall tuning — never from
querying with a smaller embedder.

## Rebuild rules

After any llama-server restart/quota/model swap:
`TIER_SMOKE=1 pytest tests/test_embedder_tier_contracts.py::test_live_four_local_models_alive_and_wired`
