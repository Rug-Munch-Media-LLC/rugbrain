# DEPRECATION NOTICE — rmi-rag

**Status: DEPRECATED as the canonical RAG implementation (2026-09-05).**

## Canonical RAG Service

The canonical RAG service is now **[rugbrain](https://git.rugmunch.io/RugMunchMedia/rugbrain)**
(`/srv/work/repos/rugbrain`). It is the deployed implementation
(`rugbrain-api:8012`) and the single source of truth for the RAG engine.

## Why

- rmi-rag and rugbrain share ~274 byte-identical files (~80K lines of duplication).
- rugbrain is the repo actually deployed and maintained.
- Keeping two near-identical copies invites drift and wasted effort.

## What Happens to rmi-rag

- rmi-rag will be **reduced to a thin client that imports from rugbrain**, or
  archived outright. No merge has been performed yet — that is a separate,
  larger operation.

## Rules for Contributors (and AI agents)

1. **Do not add new code to rmi-rag.** Add it to rugbrain instead.
2. Bug fixes should land in rugbrain first; rmi-rag receives them only if a
   thin-client shim requires it.
3. Do not deploy from rmi-rag. The live service runs from rugbrain.
4. Do not "sync" files between the two repos manually — that perpetuates the
   duplication this deprecation is meant to end.

## Questions

See `README.md` (deprecation banner at top) and the rugbrain repo's
`README.md` (canonical notice at top).
