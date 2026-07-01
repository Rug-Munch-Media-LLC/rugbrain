# RMI RAG — Retrieval-Augmented Generation Permanence

> **The RAG layer that makes RMI the smartest crypto intelligence platform.**
> Every threat, scam, label, and forensic insight is embedded, indexed, and queryable.

---

## What Is This?

This is the **RAG permanence system** for Rug Munch Intelligence. It contains:

- **20 Qdrant collections** — vector embeddings of threats, scams, labels, forensics
- **Scraper pipelines** — continuous ingestion of intel from 50+ sources
- **Embedding models** — BGE-M3 + OpenAI + Cohere configurations
- **RAG queries** — the actual retrieval logic used by `/api/v1/rag/v2/*`

## Qdrant Collections

| Collection | Purpose | Records |
|-----------|---------|---------|
| `threat_intel` | Phishing domains, exploit signatures | ~50K |
| `known_scams` | Known rugpulls, honeypots | ~30K |
| `scam_patterns` | Recurring attack patterns | ~20K |
| `wallet_profiles` | Wallet behavior fingerprints | ~100K |
| `token_analysis` | Token risk profiles | ~200K |
| `token_security` | Contract security scores | ~150K |
| `contract_audits` | Smart contract audit results | ~10K |
| `crime_reports` | Crypto crime reports | ~5K |
| `crypto_terms` | Crypto glossary embeddings | ~2K |
| `defi_hacks` | DeFi exploit history | ~15K |
| `forensic_reports` | Investigation reports | ~3K |
| `market_intel` | Market analysis | ~40K |
| `news_articles` | Crypto news | ~500K |
| `rmi_agent_memory` | Agent conversation memory | varies |
| `rmi_community` | Community contributions | varies |
| `rmi_knowledge` | General knowledge base | ~100K |
| `rmi_news` | RMI news feed | ~80K |
| `rug_timeline` | Rugpull timeline | ~8K |
| `transaction_patterns` | TX pattern analysis | ~60K |
| `vuln_patterns` | Vulnerability patterns | ~25K |

## Ingestion Sources

- **Ponder indexers** — on-chain events (ETH, BSC, Arbitrum, Polygon)
- **Web scrapers** — Pry, GoPlus, De.fi, Etherscan, CoinGecko
- **Community submissions** — community forensics API
- **AI-generated** — investigations, reports, risk profiles

## Architecture

```
Sources -> Scrapers -> Embedders -> Qdrant -> RAG API -> Consumers
                     (BGE-M3)   (20 cols)  (/rag/v2)
```

## Backups

All embeddings, collection metadata, and ingestion logs are backed up to:
- **HF S3:** `s3://rmi-rag/` bucket (weekly)
- **HF Dataset:** `cryptorugmuncher/rmi-rag-corpus`

## Development

```bash
pip install -r requirements.txt

python scripts/ingest_threats.py
python scripts/ingest_scams.py
python scripts/embed_collections.py

python scripts/query.py --collection threat_intel --query "phishing"
```

## Structure

```
rmi-rag/
  collections/     # Collection-specific configs
  embeddings/      # Embedding model configs
  scrapers/        # Ingestion source code
  scripts/         # Operational scripts
  docs/            # Architecture docs
  README.md
  requirements.txt
```

Part of RMI (Rug Munch Intelligence) — Wyoming-based.