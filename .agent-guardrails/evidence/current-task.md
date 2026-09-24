# Current task evidence

## Scope
RugBrain canonical 2560-dimensional Qwen3-Embedding-4B path. The separate 8B re-embedding services and files were not modified or restarted.

## Commands and results
- `heavy-run .venv/bin/pytest -q tests/test_qdrant_store.py tests/test_qdrant_store_write_gate.py tests/test_content_engine.py tests/test_routes_core.py tests/test_subscriber.py tests/test_main_health.py` — 44 passed.
- Related suite: `90 passed`.
- Full suite excluding the known golden-set ID failure: `1434 passed, 35 skipped, 1 deselected, 2 unrelated failures`; the remaining failures are `test_all_rows_verified` (golden data provenance) and import-linter package-path resolution.
- `heavy-run .venv/bin/ruff check` on changed source/tests — passed.
- `heavy-run .venv/bin/mypy src/rugbrain/qdrant_store src/rugbrain/main.py src/rugbrain/content_engine.py --ignore-missing-imports` — passed.
- Full required `mypy src/rugbrain` still reports 14 pre-existing errors in unrelated `news_filter.py`, `consolidation.py`, `search/engine/vector_lane.py`, and `search/filters.py`.
- Full required `ruff check src/rugbrain ...` still reports one pre-existing unused `noqa: BLE001` in `src/rugbrain/eval/harness.py`; changed-file Ruff passes.
- Live read-only Qdrant schema probe: `news_v2` is bare 2560d; `rag_documents` is named `dense` 2560d plus `sparse`; hybrid read returned two results without a vector-name error.

## Changes
- Qdrant collection checks now reject incompatible dimensions and discover configured dense/sparse names.
- Search, hybrid search, and writes fail closed before incompatible requests.
- Startup/health/readiness validate canonical Qdrant schema and require Qdrant + Redis.
- Content drafting handles an empty story set without `asyncio.wait([])`.
- Root `/embed` compatibility alias is mounted.
- Scanner subscriber/poller use Redis async close and document canonical 2560d embeddings.

## Residual risk
- No production restart, deployment, or live write smoke test has been performed yet.
- Empty retired 1024d placeholder collections remain in Qdrant and must be reviewed separately; no destructive collection deletion was performed.
- Full repository mypy/ruff gates retain unrelated baseline failures.
- Security: no secrets were added; a prior diagnostic tool output exposed secret-bearing environment values and should be handled as a credential-rotation incident.
- Performance: schema/name probes are cached per store instance; health checks add bounded Qdrant reads.
- Continuity: the canonical `/api/v1/embed` route remains unchanged; `/embed` is additive.
