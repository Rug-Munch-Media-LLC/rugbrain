# RMI RAG — Retrieval-Augmented Generation Permanence

RAG layer for Rug Munch Intelligence. Stores vector embeddings of threats, scams, labels, and forensic insights in Qdrant.

## Scope
- Qdrant collection definitions
- Embedding model configs
- Ingestion scripts from Ponder, Pry, and APIs
- RAG query helpers

## Dependencies
- Vector database (configurable via environment variables)
- Embedding service (configurable via environment variables)
- Backend service for query endpoints

## Collections
Collections are configured via the `RAG_COLLECTIONS` environment variable. See `.env.example` for available options.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/RugMunchMedia/rmi-rag.git
cd rmi-rag

# Install dependencies
pip install -r requirements.txt

# Run an ingestion script
python scripts/ingest_threats.py
```

Requires a running Qdrant instance and embedding service (configured via environment variables).

## Open-Core Model

This repo is open source (MIT). The code here demonstrates RAG patterns for
crypto intelligence. The RugMunch managed platform at rugmunch.io includes
proprietary enhancements.

See [OPEN-CORE.md](OPEN-CORE.md) for details.

## Standards
This repo follows Rug Munch Media standards:
https://github.com/RugMunchMedia/standards
