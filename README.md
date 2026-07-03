# RMI RAG — Retrieval-Augmented Generation Permanence

RAG layer for Rug Munch Intelligence. Stores vector embeddings of threats, scams, labels, and forensic insights in Qdrant.

## Scope
- Qdrant collection definitions
- Embedding model configs
- Ingestion scripts from Ponder, Pry, and APIs
- RAG query helpers

## Fleet dependencies
- Qdrant at 127.0.0.1:6333 (currently down due to stale IP bind)
- bge-m3 embeddings at 127.0.0.1:8001
- RMI backend for query endpoints

## Collections
See collections/ for per-collection configs. 20 collections including threat_intel, known_scams, wallet_profiles, token_analysis, news_articles.

## Development
pip install -r requirements.txt
python scripts/ingest_threats.py

## Standards
This repo follows Rug Munch Media standards:
https://git.rugmunch.io/RugMunchMedia/standards
