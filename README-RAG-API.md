# RugBrain API — Shared Threat Intelligence

RugBrain (formerly RMI-RAG) provides semantic search and scam detection across
the RugMunch fleet. All RugMunch apps can query it for token safety, known
scam patterns, and threat intelligence.

## Base URL

| Environment | URL |
|-------------|-----|
| Production  | `https://rugbrain.rugmunch.io/api/v1` |
| Local       | `http://localhost:8012/api/v1` |
| Docker      | `http://rugbrain:8012/api/v1` |

## Authentication

None required for read-only queries. Rate limits apply (see below).

## Endpoints

### Search Threat Intel

```
GET /api/v1/search?q=<query>&limit=10&collection=threat_intel
```

Semantic search across ingested scanner findings. Returns similar results
by vector similarity (Qwen3-Embedding-4B via llama.cpp, 2560 dimensions).

**Parameters:**
- `q` (required) — search query, e.g., token address or scam description
- `limit` (optional, default 10, max 100) — max results
- `collection` (optional, default `threat_intel`) — Qdrant collection

**Example:**
```bash
curl "http://localhost:8012/api/v1/search?q=0xdead...&limit=5"
```

### Scam Check (v2)

```
GET /api/v1/rag/v2/scam-check?address=<address>&chain=<chain>&limit=5&tier=free
```

Checks a token address against known scams and threat patterns using
a three-pillar search (dense vectors + sparse BM25 + cross-encoder reranking).

**Parameters:**
- `address` (required) — token contract address
- `chain` (optional, default `ethereum`) — blockchain identifier
- `limit` (optional, default 10, max 100) — max matches
- `tier` (optional, `free` or `pro`) — rate limit tier

**Response:**
```json
{
  "address": "0x...",
  "chain": "ethereum",
  "scam_risk": "high" | "medium" | "low" | "unknown",
  "confidence": 85,
  "matches": [
    {
      "id": "...",
      "content": "Scam description...",
      "similarity": 0.87,
      "severity": "critical",
      "collection": "known_scams",
      "source": ""
    }
  ],
  "total_matches": 3
}
```

### Confidence Score

```
POST /api/v1/rag/confidence
```

Score the confidence of a result set against a query.

### Deployer Risk

```
POST /api/v1/scanner/deployer-risk
```

Score a token deployer's risk based on deployment history, funding sources,
and code similarity.

## Rate Limits

| Tier  | Limit          | Use Case              |
|-------|----------------|-----------------------|
| free  | 5 requests/hr  | Bot integrations      |
| pro   | 100 requests/hr| Production apps       |

Rate limit is tracked by `X-Device-Id` header or client IP. A `429`
response includes `X-RateLimit-Reset` header with seconds until reset.

## Usage Examples

### Python (httpx)

```python
import httpx

async with httpx.AsyncClient(timeout=5) as client:
    resp = await client.get(
        "http://rugbrain:8012/api/v1/rag/v2/scam-check",
        params={"address": "0x...", "chain": "ethereum", "limit": 5},
    )
    if resp.status_code == 200:
        data = resp.json()
        print(f"Risk: {data['scam_risk']} (confidence: {data['confidence']}%)")
```

### cURL

```bash
curl -s "http://localhost:8012/api/v1/rag/v2/scam-check?address=0x...&chain=ethereum" | jq
```

### Rust (reqwest)

```rust
let resp = reqwest::get(
    "http://rugbrain:8012/api/v1/rag/v2/scam-check?address=0x...&chain=ethereum"
).await?.json::<serde_json::Value>().await?;
```

### JavaScript/TypeScript

```typescript
const resp = await fetch(
  "http://rugbrain:8012/api/v1/rag/v2/scam-check?address=0x...&chain=ethereum"
);
const data = await resp.json();
console.log(data.scam_risk);
```

## Current Consumers

| App | Integration Point | Status |
|-----|------------------|--------|
| rmi-backend | Internal RAG for scanner result enrichment | LIVE |
| rugcharts | `GET /api/v1/rugcharts/intel/{chain}/{pair}` — threat_intel field | WIRED |
| rugmunchbot | Scan result enrichment (via RAG_URL in config) | READY |
| rugmaps | Token map risk overlays | PLANNED |
| walletpress | Wallet address scam check | PLANNED |

## Architecture

```
┌──────────┐   HTTP    ┌──────────────────────────────────────┐
│  Client  │ ────────▶ │  rugbrain (FastAPI, port 8012)       │
│  App     │           │                                      │
└──────────┘           │  ┌────────┐  ┌──────────────────┐   │
                       │  │ Qdrant │  │ Qwen3 Embed   │   │
                       │  │ Vector │  │ nomic-embed-text │   │
                       │  │ Store  │  │ 768d             │   │
                       │  └────────┘  └──────────────────┘   │
                       │  ┌──────────────────────────────┐   │
                       │  │ Redis (rate limit + cache)   │   │
                       │  └──────────────────────────────┘   │
                       │  ┌──────────────────────────────┐   │
                       │  │ BM25 Sparse Index            │   │
                       │  └──────────────────────────────┘   │
                       └──────────────────────────────────────┘
```

## Security Guardrails

RugBrain applies three additive security guardrails on the RAG answer path
(`src/rugbrain/guardrails.py`, wired into `mcp_bridge.py`):

1. **Prompt-injection defense** — every user query is passed through
   `sanitize_query()` before it is embedded or forwarded to the MCP bridge.
   Instruction-override phrases ("ignore previous instructions"), role
   switches ("system:", "you are now"), and fenced-code injection are
   detected, flagged, and neutralized. When a flagged query strips to
   nothing usable, the bridge refuses it (fail-closed) and returns a
   `guard: {refused: true, reasons: [...]}` payload instead of touching the
   MCP.

2. **Secret-leak redaction** — all result content returned by the bridge is
   run through `redact_output()` to strip EVM private keys (`0x` + 64 hex),
   labeled 64-hex keys, 12/24-word mnemonic phrases, API-key-looking
   strings, internal fleet hostnames (`*.rugmunch.io`), and private IPs
   (RFC1918 + CGNAT).

3. **RAG-poisoning filter** — `trusted_filter()` drops (or ranks down)
   chunks whose metadata lacks a trusted `source_type`/`source` or carries a
   `poisoned`/`unverified` flag. `tag_unverified()` marks docs at ingest.

### "Never query user-uploaded partitions" rule (Qdrant)

User-uploaded documents live in dedicated partitions and MUST NOT be
searched by the shared RAG answer path. Every search on shared collections
must exclude them at the payload level using
`build_trusted_partition_filter()`:

```python
from rugbrain.guardrails import build_trusted_partition_filter
from rugbrain.qdrant_store import QdrantVectorStore

store = QdrantVectorStore()
results = await store.search(
    collection="rag_docs",
    vector=...,
    filters=build_trusted_partition_filter(),  # {"must_not": [...]} on partition
)
```

The helper returns a Qdrant `must_not` filter excluding
`user_upload` / `user-uploaded` partitions; combine it with any `must`
filters the caller already applies. Chunks without source metadata are
treated as untrusted by `trusted_filter` (default-deny).

Guardrails are additive and fail-open-safe: a guardrail error degrades to
passthrough so the bridge never breaks. Set `RMI_GUARDRAILS=0` to disable
input sanitization / output redaction for local debugging.
