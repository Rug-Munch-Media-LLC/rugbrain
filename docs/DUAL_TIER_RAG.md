# Dual-Tier RAG — Production Architecture

> Status: **LIVE** (speed tier + chat deployed 2026-08-23; deep tier pre-existing)
> Owner: build lead · Review cadence: after any model swap

## 1. Topology

```
                 Incoming consumer query
                          |
                [ word-count router: <45 words? ]
                 /                        \
            SPEED TIER                  DEEP TIER
   Qwen3-Embedding-0.6B :8007    Qwen3-Embedding-4B :8004 (pre-existing)
        recall 80 (1024-dim)          recall 30 (2560-dim)
            |                              |
   Qwen3-Reranker-0.6B :8008      Qwen3-Reranker-4B :8003 (pre-existing)
            \                            /
             \        top-5 contexts    /
              └────────────┬───────────┘
                    Qwen3-8B-Instruct :8009
                    (llama-chat.service)
```

Qdrant collection `crypto_intelligence` — dual named vectors per point:
`qwen_vector` (2560, COSINE) · `speed_vector` (1024, COSINE).

## 2. Usage policy — which tier serves what

| Workload | Tier | Why |
|---|---|---|
| Consumer chat retrieval (<45 words) | speed | latency-critical short queries |
| Deep analysis / forensics pipelines (≥45 words) | deep | precision over latency |
| Telegram/bot quick lookups | speed | interactive SLA |
| Batch research / re-scoring jobs | deep | quality over speed |
| Ingestion | BOTH | every doc gets both vectors |

**Why Qwen3-0.6B and not ModernBERT for the speed tier:** live A/B on
2026-08-23 showed gte-reranker-modernbert ranking rug-pull evidence BELOW a
healthy-pool document; Qwen3-Reranker-0.6B with the instruction library
separates them sharply (0.995 vs 0.208). Family consistency with the deep
tier means one instruction library works everywhere.

## 3. Instruction awareness (critical)

Both rerankers are instruction-aware. Our llama.cpp build ignores the
`/rerank` `instruct` field, so vertical instructions are embedded into the
query slot (`[TASK] … [QUERY] …`) by `rugbrain.search.rerank.apply_instruction`.
Measured effect: forensic-evidence doc 0.001 → 0.99 on a rug-pull query.
Kill-switch: `RERANK_INSTRUCTIONS=off`.

## 4. Code map

| Piece | Path |
|---|---|
| Retriever/routing/fail-open | `src/rugbrain/search/dual_tier.py` |
| Instruction library | `src/rugbrain/search/rerank.py` |
| Schema creation/verify | `scripts/dual_tier/schema_setup.py` |
| Corpus backfill (speed_vector) | `scripts/dual_tier/backfill_speed_vectors.py` |
| Tests | `tests/test_dual_tier.py`, `tests/test_rerank_instructions.py` |

## 5. Ops runbook

### Health check (all five engines)
```bash
for p in 8003 8004 8007 8008 8009; do
  printf "$p: "; curl -s http://172.22.0.1:$p/health; echo
done
```

### Create / verify the collection
```bash
cd /srv/work/repos/rmi-rag && .venv/bin/python -m scripts.dual_tier.schema_setup
```

### Backfill speed_vector (AFTER embed-migration completes)
```bash
python -m scripts.dual_tier.backfill_speed_vectors --batch-size 64 --sleep-secs 0.5
# refuses while rag-embed-migration is active unless --force
# resumable: payload marker speed_embedded=true skips completed points
```

### Rollback / kill-switches
| Env var | Effect |
|---|---|
| `RERANK_INSTRUCTIONS=off` | plain relevance ranking (pre-instruction behavior) |
| `RERANK_ENABLED=false` (rag lane) | skip reranking entirely |
| `ROUTING_THRESHOLD_WORDS` | move the tier boundary (default 45) |

### Service units (all Restart=always, enabled at boot)
`llama-speed-embed.service` (:8007, MemoryMax=3G, CPUQuota=400%)
`llama-speed-rerank.service` (:8008, MemoryMax=3G, CPUQuota=400%)
`llama-chat.service` (:8009, MemoryMax=9G, CPUQuota=1000%, mmap+--no-repack)
Deep tier: `qwen-embedder.service` (:8004), `qwen-reranker.service` (:8003).

## 6. Testing

```bash
pytest tests/test_dual_tier.py tests/test_rerank_instructions.py -q
```
Covers: word-count routing boundary, per-tier endpoint selection, recall
limits, fail-open chain (speed→deep→empty), instruction embedding in rerank
queries, noise-floor filtering, kill-switch env handling. All mocked — no
live services needed.

## 7. Known constraints & follow-ups

1. Chat decode is contention-throttled while rag-embed-migration runs;
   re-benchmark latency after it completes.
2. Backfill not yet executed (guarded against the migration).
3. Community chat bot wiring to :8009 pending (rugmunchbot chat_ai lane).
4. If upstream llama.cpp adds native `instruct` passthrough, migrate the
   query-slot trick to the real field and re-run the A/B.

## 8. Chatbot cutover runbook (OpenRouter → local :8009)

### Pre-cutover gate
```bash
# 1. Live stack validation (all must pass):
RUN_LIVE_TESTS=1 pytest tests/integration/test_dual_tier_live.py -v
# 2. Shadow A/B vs OpenRouter (does NOT switch anything):
OPENROUTER_API_KEY=<key> python -m scripts.dual_tier.shadow_test_chat --runs 3
#    Require: >=90% local pass rate before cutover.
```

### Cutover (rugmunchbot community chat)
```bash
# rugmunchbot chat_ai lane: point chat tier at the local engine.
# In rugmunchbot env/config: CHAT_PROVIDER=local CHAT_LOCAL_URL=http://172.22.0.1:8009/v1
# Rollback = flip CHAT_PROVIDER back to openrouter (instant, no restart of llama services).
```

### Post-migration checklist (embed migration MUST be complete first)
- [ ] `systemctl enable --now llama-chat llama-speed-embed llama-speed-rerank`
- [ ] Re-run live integration suite — expect full green incl. latency budgets
- [ ] `python -m scripts.dual_tier.backfill_speed_vectors` (guarded, resumable)
- [ ] Verify Qdrant counts: qwen_vector == speed_vector coverage
- [ ] Shadow test re-run at unloaded host for true latency numbers
- [ ] Cutover per above; monitor first 24h via :8009 --metrics
