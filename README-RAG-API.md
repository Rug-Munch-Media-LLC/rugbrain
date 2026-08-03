# RugSense API — Shared Threat Intelligence

RugSense (formerly RMI-RAG) provides semantic search and scam detection across
the RugMunch fleet. All RugMunch apps can query it for token safety, known
scam patterns, and threat intelligence.

## Base URL

| Environment | URL |
|-------------|-----|
| Production  | `https://rmi-rag.rugmunch.io/api/v1` |
| Local       | `http://localhost:8012/api/v1` |
| Docker      | `http://rmi-rag:8012/api/v1` |

## Authentication

None required for read-only queries. Rate limits apply (see below).

## Endpoints

### Search Threat Intel

```
GET /api/v1/search?q=<query>&limit=10&collection=threat_intel
```

Semantic search across ingested scanner findings. Returns similar results
by vector similarity (Infinity BAAI/bge-m3, 1024 dimensions).

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
        "http://rmi-rag:8012/api/v1/rag/v2/scam-check",
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
    "http://rmi-rag:8012/api/v1/rag/v2/scam-check?address=0x...&chain=ethereum"
).await?.json::<serde_json::Value>().await?;
```

### JavaScript/TypeScript

```typescript
const resp = await fetch(
  "http://rmi-rag:8012/api/v1/rag/v2/scam-check?address=0x...&chain=ethereum"
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
│  Client  │ ────────▶ │  rmi-rag (FastAPI, port 8012)       │
│  App     │           │                                      │
└──────────┘           │  ┌────────┐  ┌──────────────────┐   │
                       │  │ Qdrant │  │ Infinity Embed │   │
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
