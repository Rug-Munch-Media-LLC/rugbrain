# Embedding & Reranking Stack — Unified Configuration Reference

> **The one doc** for how every product in the fleet embeds and reranks.
> Verified live 2026-08-24. If code and this doc disagree, fix the code.

## Model Inventory (self-hosted llama.cpp on Talos)

| Service | Port | Model | Output Dims | Role |
|---------|------|-------|-------------|------|
| qwen-embedder | :8004 | qwen3-embedding-4b Q8_0 | **2560** | Corpus embeddings + search queries (local) |
| qwen-reranker | :8003 | qwen3-reranker-4b Q8_0 | scores | Deep rerank (local) |
| DeepInfra (hosted) | API | `Qwen/Qwen3-Embedding-4B` + `Qwen/Qwen3-Reranker-4B` | 2560 / scores | **Canonical primary lane** (same models as local) |
| model gateway | :8010 | — | — | Strain breaker + cloud fallback lanes |
| chat | :8009 | qwythos-9b | — | LLM generation only |

> **Corrections (2026-09-20, verified against the running services):** output
> dims are **2560** (not 1024/MRL — every collection measures 2560d). The
> 0.6B utility tiers (`llama-speed-embed` :8007, `qwen-reranker-fast` :8008)
> are **retired** — both ports are dead. There is no BGE-M3 anywhere in the
> stack: embed = `Qwen3-Embedding-4B`, rerank = `Qwen3-Reranker-4B`, local
> llama.cpp + DeepInfra.

**Bind reality (2026-09-20):** `:8003`/`:8004` listen on **127.0.0.1**
(verified with `ss`); the historical docker-bridge (`172.22.0.1`) guidance
predates the current units — check `ss -tlnp` before pointing a client, and
never assume a bind from this doc alone.

## The Two Iron Rules

1. **Cross-model mismatch**: same dims ≠ same space. A 0.6B query against
   a 4B-embedded collection returns confident garbage. Corpus search and
   ingestion are DEEP-tier only; the fast tier serves utility tasks where
   both sides run 0.6B.
2. **Free-first always**: local servers → OpenRouter paid (backup acct)
   → OpenRouter free (main acct). Cloud is a fallback, never a default.

## Tier Matrix Per Product (2026-09-20)

| Product | Embed | Rerank | Chat |
|---------|-------|--------|------|
| **rmi-backend** | `Qwen3-Embedding-4B` (DeepInfra + local :8004) via rugbrain | via rugbrain | — |
| **rugbrain** | `Qwen3-Embedding-4B` (DeepInfra canonical → local :8004) | `Qwen3-Reranker-4B` :8003 + DeepInfra hosted | `qwythos-9b` :8009 (`RAG_CHAT_MODEL`) |
| **rugmunchbot** | :8004 via its router | — | **intent-routed**: `local/qwythos-9b` first (free, private) → cloud for quality-critical |
| **lite-chain-indexer** | own pools | — | — |

## Fallback Ladders (2026-09-20)

### Embedding
```
DeepInfra Qwen/Qwen3-Embedding-4B   (canonical primary, 2560d, keyed)
  ↓ unreachable
local llama.cpp :8004               (SAME model ⇒ index-compatible fallback)
  ↓ both down
FAIL LOUD  (RAGUnavailableError → HTTP 503; no hash, no 0.6B, no MRL)
```

### Reranking
```
local :8003 Qwen3-Reranker-4B (Q8_0)
  ↓ strained/unreachable
DeepInfra Qwen/Qwen3-Reranker-4B (hosted, same model)
  ↓ both down
FAIL-OPEN: pre-rerank order preserved (search never breaks)
```

### Chat (local-free first — "fast response" lane)
```
local qwythos-9b :8009              (our model; free, private, ~8-10 tok/s CPU)
  ↓ quality-critical / latency budget exceeded
cloud lanes via the consumer's router (bot: intent table in model_router.py)
```

## Environment Variables

| Var | Default | Used by |
|-----|---------|---------|
| `QWEN_EMBED_URL` | http://127.0.0.1:8004/v1 | backend dispatcher, rugbrain |
| `QWEN_FAST_URL` | (retired — :8007 dead) | backend dispatcher |
| `RERANKER_URL` | http://172.22.0.1:8003 | backend + rugbrain rerank |
| `RERANKER_URL_FAST` | http://172.22.0.1:8008 | rugbrain + backend live tier |
| `RERANKER_LIVE_CANDIDATE_CAP` | 40 | live candidate cap |
| `RERANKER_LIVE_TIMEOUT` | 6.0 | live budget (s) |
| `RPC_ERROR_RATE_DAMPING` | 2.0 | indexer P2C success damping |
| `OPENROUTER_BACKUP_API_KEY` | gopass | paid embedding fallback |
| `OPENROUTER_API_KEY` | gopass | free embedding fallback |

## Incident History (learned the hard way)

1. **2560-dim ingestion breaker** (2026-08-24): server serves MRL 1024;
   code demanded 2560 → every ingestion embed raised. Fix: DIMS=1024 +
   truncate-oversized policy. Lesson: dims come from the LIVE server,
   not the model card.
2. **localhost defaults** (2026-08-24): 127.0.0.1:8003 refused — services
   bind the bridge IP. RugBrain's deep rerank had never served. Lesson:
   defaults must match deployment reality; verified by curl, not docs.
3. **Dictionary citations** (2026-08-23): SearXNG matched filler words
   ("give") → irrelevant pages cited as sources. Fix: query cleaning +
   keyword-overlap filter + source-integrity prompt rules.

## Verification Commands

```bash
# All four servers serving correct models/dims:
curl -s http://172.22.0.1:8004/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"input":["t"],"model":"qwen3-embedding-4b"}' | jq '.data[0].embedding | length'   # 1024
curl -s -X POST http://172.22.0.1:8008/rerank -H 'Content-Type: application/json' \
  -d '{"query":"rug","documents":["a","b"]}' | jq '.results[0].relevance_score'

# Dispatcher order (backend):
python3 -c "from app.rate_limiter import configure_providers; configure_providers(True)"

# Migration health:
systemctl is-active migrate-embeds.service
```
