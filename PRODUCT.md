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
- Embedding model configs (BGE-M3, OpenAI, Cohere)
- Ingestion scripts from Ponder, Pry, and APIs
- RAG query helpers

## Anti-scope
- No FastAPI gateway (lives in rmi-backend)
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
