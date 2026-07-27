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

## Development
pip install -r requirements.txt
python scripts/ingest_threats.py

## Standards
This repo follows Rug Munch Media standards:
https://git.rugmunch.io/RugMunchMedia/standards
