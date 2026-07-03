# RMI RAG — Architecture

**Status:** canonical
**Last updated:** 2026-07-03

## Components
- collections/ — Qdrant collection configs
- embeddings/ — model configs
- scrapers/ — ingestion source code
- scripts/ — operational scripts

## Data flow


## Fleet dependencies
- Qdrant at 127.0.0.1:6333 (currently down due to stale IP bind)
- bge-m3 embeddings at 127.0.0.1:8001

## Current issues
- README references old Tailscale IP
- No automated backups (old HF S3 bucket references)
- No CI
