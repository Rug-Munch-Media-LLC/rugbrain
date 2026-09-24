# Current task evidence

## Scope
RugBrain public crypto-intelligence audience boundary and canonical 2560-dimensional Qwen3-Embedding-4B path. The separate 8B re-embedding services and files were not modified or restarted.

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
- Public audience boundary: `knowledge_boundary.py` allowlists product-owned crypto-intelligence collections, rejects internal ContextCrypto collections, and sanitizes public payloads/storage metadata.
- Public search and ingest routes reject internal collection names without echoing them; public coding search no longer merges raw ContextCrypto code by default.
- Public `/search/crypto-code` now reads only the reviewed `public_crypto_patterns` projection; approved public/open-source snippets may include code, public URLs, and licenses, while internal apps/repos remain unavailable.
- Public projection code records require `source_kind=contextcrypto_public_code`, `public_safe=true`, a public upstream URL, language, library, and a leak-scanned snippet.
- Public code requests to weaponize/execute attacks are refused; defensive/public-advisory/authorized-review questions remain supported.
- Raw ContextCrypto code retrieval is internal-only; public `/embed` no longer returns provider identity.
- Design/spec updates: `docs/RAG-MCP-BOUNDARY.md` accepted v1 audience/projection rules; `docs/DATA-PLACEMENT.md` and `docs/PROJECT_STATE.md` updated.
- Boundary/public-code/projection regression suite: `120 passed`.
- Changed-source Ruff and targeted `.venv/bin/mypy` — passed.
- Exact `ruff check` changed-file command — passed; exact focused pytest command — `120 passed`.
- A PATH-level `mypy` invocation resolves a different environment and reports 64 pre-existing import/stub errors; the repository `.venv/bin/mypy` targeted gate is clean.
- Projection importer dry-runs validated both pattern and public-code samples; no live projection was written.
- No public ContextCrypto projection is populated yet; raw internal code and source coordinates remain internal-only.
- 8B cutover gate remains closed: the active re-embed process is still processing `codebase_reference_v2` at approximately 290k/2.13M points; `agent_vectors_8b` is 0/141 and multiple targets are incomplete. No 8B routing or service cutover was performed.

## Required command evidence
- `pytest -q tests/test_qdrant_store.py tests/test_qdrant_store_write_gate.py tests/test_content_engine.py tests/test_routes_core.py tests/test_subscriber.py tests/test_main_health.py tests/test_routes_ingest.py tests/test_routes_search.py tests/test_rag_tools_boundary.py tests/test_crypto_code_retrieval.py tests/test_knowledge_boundary.py tests/test_public_projection.py tests/test_public_crypto_code.py tests/test_search_quality.py tests/test_mcp_bridge.py` — executed via `heavy-run pytest`; 120 passed.
- `ruff check src/rugbrain/knowledge_boundary.py src/rugbrain/public_projection.py src/rugbrain/public_crypto_code.py src/rugbrain/routes/search.py src/rugbrain/routes/ingest.py src/rugbrain/routes/core.py src/rugbrain/routes/rag_tools.py src/rugbrain/crypto_code_retrieval.py src/rugbrain/search/engine/knowledge.py scripts/ingest_public_contextcrypto.py tests/test_knowledge_boundary.py tests/test_public_projection.py tests/test_public_crypto_code.py tests/test_routes_ingest.py` — executed via `heavy-run ruff check`; passed.
- `.venv/bin/mypy src/rugbrain/knowledge_boundary.py src/rugbrain/public_projection.py src/rugbrain/public_crypto_code.py src/rugbrain/routes/search.py src/rugbrain/routes/ingest.py src/rugbrain/routes/core.py src/rugbrain/routes/rag_tools.py src/rugbrain/crypto_code_retrieval.py src/rugbrain/search/engine/knowledge.py --ignore-missing-imports` — executed via `heavy-run .venv/bin/mypy`; passed.
- `npx agent-guardrails check --review --commands-run ...` — passed with 0 errors and 9 warnings; remaining warnings concern pre-existing/generated files and broad multi-file state changes.
