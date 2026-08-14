# Legacy Modules Archive

These modules were archived on 2026-08-05 during a codebase pruning operation.
They had **zero references** in the codebase (confirmed via `grep -rn` across all `.py` files)
and were marked for deletion in `ARCHITECTURE-V2.md` and `TODO.md`.

## Archived Modules

| Module | Size | Reason for Archive |
|--------|------|--------------------|
| `analytics_storage.py` (16KB) | ARCHITECTURE-V2.md §3.1: DELETE. No target-state consumer. |
| `eval_ragas.py` (37KB) | RAGAS framework evaluation. Merged into `evaluation.py`. |
| `github_feeder.py` (7KB) | ARCHITECTURE-V2.md decision: DELETE. No references. |
| `query_rewriter.py` (3KB) | ARCHITECTURE-V2.md §3.1: DELETE. Dead once __init__ slimmed. |
| `rag_ingestion.py` (9KB) | ARCHITECTURE-V2.md §3.1: DELETE. _embed_batch never embeds (verified bug). |

## Affected References in Docs

These modules were referenced in documentation (ARCHITECTURE-V2.md, TODO.md, ARCHITECTURE.md)
but had zero Python-level imports. Doc references should be updated separately.

## Recovery

To restore a module, move it back from `_legacy/` to `src/rugbrain/`:
```bash
mv src/rugbrain/_legacy/<module>.py src/rugbrain/
```
