# RugBrain — Product Charter

**Product:** RugBrain (formerly RMI RAG) Permanence Layer
**Repo:** github.com/RugMunchMedia/rugbrain
**Status:** Active development

## Problem
RMI needs long-term, queryable memory of threats, scams, labels, and forensic insights beyond raw database rows.

## User
- RMI backend  endpoints
- AI agents summarizing risk reports
- Investigators searching historical patterns

## Scope
- Qdrant collection definitions and configs
- Embedding model config: **ONE stack** — `Qwen/Qwen3-Embedding-4B` (2560d,
  DeepInfra + local llama.cpp `:8004`) and `Qwen/Qwen3-Reranker-4B` (`:8003` +
  DeepInfra). No BGE/OpenAI/Cohere embedding configs.
- Ingestion scripts from Ponder, Pry, and APIs
- RAG query helpers

## Anti-scope
- ~~No FastAPI gateway~~ **Corrected 2026-09-20:** the FastAPI gateway IS part
  of rugbrain (`rugbrain-api.service`, 127.0.0.1:8012, prod
  `rugbrain.rugmunch.io/api/v1`) — routes in `src/rugbrain/routes/`.
- No web scraping engine (lives in pryscraper)
- No token scanning logic (lives in rmi-backend)

## Success criteria
- All 20 Qdrant collections documented
- Ingestion scripts runnable and scheduled
- Collection backups verified
- No file >500 lines

## Current reality
- README exists but references old Tailscale IP 
- 20 collections described but configs are minimal
- No CI, no Makefile, no pre-commit
- Tiny codebase, easy to govern
