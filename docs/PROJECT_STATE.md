# Project State

Last updated: 2026-09-24

## Goal

Keep RugBrain on one canonical Qwen3-Embedding-4B vector space (2560 dimensions) and make ingestion/readiness fail visibly when Qdrant schemas or upstream dependencies diverge.

## Current focus

Validate and ship the canonical 2560-dimensional Qdrant/search/ingestion path without touching the separate 8B re-embedding work.

## Done recently

- Added dimension and vector-name validation to Qdrant collection reads and writes.
- Added dynamic dense/sparse vector-name discovery for hybrid collections.
- Removed the retired 1024-dimensional startup collection bootstrap.
- Added schema-aware health/readiness checks and an additive `/embed` compatibility route.
- Made empty content-draft generation safe when no stories are available.
- Focused regression suite: 44 passed; changed-file Ruff and targeted mypy passed.

## Blockers

- Full-repository mypy and Ruff gates retain unrelated baseline failures.
- Production has not yet been restarted with the branch changes.
- Retired empty 1024-dimensional Qdrant placeholders require a separate migration decision.

## Next step

Run the guardrail check, review the branch diff, commit only intended RugBrain changes, push `fix/rugbrain-canonical-2560-rag`, then restart and smoke-test only RugBrain.
