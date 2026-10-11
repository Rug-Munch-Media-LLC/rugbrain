# RAG Architecture

## Data Flow

1. **Ingestion** — Scrapers fetch from 50+ crypto sources
2. **Normalization** — Parse HTML/JSON into structured data
3. **Embedding** — Qwen3-Embedding-8B (2560-dim) generates vector embeddings
4. **Storage** — Qdrant collections (20 total)
5. **Retrieval** — Semantic search via RAG v2 search endpoint
6. **Augmentation** — Context injection into LLM prompts
7. **Generation** — AI agents use retrieved context for answers

## Collections Schema

Each Qdrant collection follows this structure:

```python
{
  "id": "uuid",
  "vector": [2560 floats],  # Qwen3-Embedding-8B embedding
  "payload": {
    "source": "etherscan|goPlus|community|...",
    "category": "threat|scam|token|wallet|...",
    "severity": "low|medium|high|critical",
    "chain": "ethereum|bsc|solana|...",
    "address": "0x..." (optional),
    "text": "raw text content",
    "metadata": {...},
    "timestamp": "ISO 8601",
    "confidence": 0.0-1.0
  }
}
```

## Update Schedule

| Collection | Frequency | Source |
|-----------|-----------|--------|
| threat_intel | Hourly | GoPlus, PhishTank |
| known_scams | Hourly | Chainabuse, community |
| wallet_profiles | Daily | Ponder indexers |
| token_security | On-demand | Contract scanners |
| news_articles | Every 5 min | RSS feeds |
| rmi_news | Every 15 min | Internal scrapers |
| rmi_community | On-submit | Community forensics API |
| rmi_agent_memory | Per-session | Agent conversations |

## Embedding Pipeline

- **Primary model:** Qwen3-Embedding-8B (2560 dimensions)
- **Fallback:** OpenAI text-embedding-3-small
- **Batch size:** 64 texts per batch
- **GPU:** Optional (CPU works at ~100 texts/sec)

## Query Performance

- **p50 latency:** 45ms
- **p99 latency:** 180ms
- **Throughput:** 500 QPS per collection
- **Cache hit rate:** 60% (Redis)

Part of RMI — Wyoming-based.