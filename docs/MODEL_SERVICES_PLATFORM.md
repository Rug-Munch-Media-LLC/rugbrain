# Model Services — Platform Usage Doctrine

> The Qwen3 lineup is a **platform utility**, not an RAG-internal detail.
> Any RugMunch component consumes it through ONE door: the Model Services
> Gateway (`:8010`, `model_gateway.service` on Talos).

## 1. The lineup

| Model | Port | Role | Strengths |
|---|---|---|---|
| Qwen3-Embedding-**4B** | :8004 | DEEP embed | 2560-dim precision, 32K ctx, multilingual |
| Qwen3-Reranker-**4B** | :8003 | DEEP rerank | high-precision ordering |
| Qwen3-Embedding-**0.6B** | :8007 | SPEED embed | 1024-dim, ~20ms/query |
| Qwen3-Reranker-**0.6B** | :8008 | SPEED rerank | fast scoring, sharp w/ instructions |
| Qwen3-8B-Instruct | :8009 | CHAT/generation | community chat, summaries |

All CPU-served (llama.cpp b10155) on the RS8000; cgroup-capped so no engine
can starve another. All rerankers share the vertical instruction library
(`rugbrain.search.rerank`) — forensics / tracing / contract / code.

## 2. When we rely on WHICH tier

### Deep tier (4B pair) — reach for it when being WRONG is expensive
- Forensic analysis pipelines (rug-pull verdicts, fund-tracing reports)
- Batch research / re-scoring of stored intelligence
- Ingestion embedding of the canonical corpus (`qwen_vector`)
- Any query ≥45 words arriving through the gateway (auto-routed)

### Speed tier (0.6B pair) — reach for it when being SLOW is expensive
- Consumer/community chat retrieval (<45 words, auto-routed)
- Telegram/bot interactive lookups
- UI autocomplete / typeahead semantic search
- High-volume classification pre-passes (dedup, near-duplicate clustering)
- Ingestion embedding of the mirror vector (`speed_vector`)

### Rule of thumb
> **Deep = correctness per document. Speed = correctness per second.**
> If a human waits interactively → speed. If a pipeline batches → deep.
> When unsure, send through the gateway and let the word-count router decide.

## 3. Integration door — the Gateway (:8010)

```bash
# retrieve (auto-tier)
curl -X POST http://172.22.0.1:8010/retrieve -H 'Content-Type: application/json' \
  -d '{"query":"is this token a rug pull?","top_k":5,"collection":"crypto_intelligence"}'

# ask (retrieve + generate in one call)
curl -X POST http://172.22.0.1:8010/ask -H 'Content-Type: application/json' \
  -d '{"query":"summarize rug-pull signals for TokenX"}'

# embed any text at any tier
curl -X POST http://172.22.0.1:8010/embed -H 'Content-Type: application/json' \
  -d '{"text":"deployer owns 85% of LP","tier":"speed"}'

# rerank any candidate list (instructions auto-applied by vertical)
curl -X POST http://172.22.0.1:8010/rerank -H 'Content-Type: application/json' \
  -d '{"query":"rug pull warning signs","documents":["...","..."]}'
```

Collections are **per-domain namespaces** in the same Qdrant: today
`crypto_intelligence`; tomorrow anything. Same engines, same policy.

## 4. Platform use cases (current + planned)

| Component | Use case | Tier | Status |
|---|---|---|---|
| rmi-rag search lane | dual-tier RAG retrieval | both | ✅ LIVE |
| Community chat (rugmunchbot) | answers via :8009 + speed recall | speed+chat | 🔜 wiring |
| rmi-backend rag module | analysis pipelines | deep | ✅ (instruction lib live) |
| **pryscraper** | **scraping-methodology memory**: store "how we detected scraper X" playbooks as vectors; new engineers/agents query "how do we scrape site-class Y" and get proven recipes | speed | 📋 recipe ready (§5) |
| **walletpress** | wallet-intel doc memory: entity write-ups, attribution notes searchable semantically | speed | 📋 same recipe |
| **degenfeed** | near-duplicate content collapse across feeds (embed + cosine gate) | speed | 📋 same recipe |
| backend entity resolution | fuzzy name/alias shortlist reranking before graph walk | deep | 💡 future |

The pattern is always identical: **embed domain docs once (both tiers if they
join the main corpus; speed-only for private domain stores), then query via
the gateway.** No component talks to llama.cpp directly anymore.

## 5. Recipe — spin up a domain memory store (e.g. pry_methodology)

```bash
# 1. Create the collection (same dual-vector layout):
python - <<'EOF'
from qdrant_client import QdrantClient, models
c = QdrantClient(url="http://localhost:6333")
c.create_collection("pry_methodology", vectors_config={
    "qwen_vector":  models.VectorParams(size=2560, distance=models.Distance.COSINE),
    "speed_vector": models.VectorParams(size=1024, distance=models.Distance.COSINE)})
print("created")
EOF

# 2. Ingest playbook docs (dual embed, same as crypto_intelligence ingest):
#    reuse scripts/dual_tier ingestion pattern with collection="pry_methodology"

# 3. Query from anywhere via the gateway:
curl -X POST http://172.22.0.1:8010/retrieve -H 'Content-Type: application/json' \
  -d '{"query":"how do we bypass cloudflare on ecommerce sites?","collection":"pry_methodology"}'
```

## 6. Guardrails & ops

- Gateway health aggregates all five engines: `GET :8010/health`
- Kill-switches: `RERANK_INSTRUCTIONS=off`, `ROUTING_THRESHOLD_WORDS`
- Cgroup caps prevent cross-tier starvation (see unit files in fleet-infra)
- Chat latency is contention-sensitive: while bulk jobs run, expect slow
  :8009 answers; the migration end restores full speed
