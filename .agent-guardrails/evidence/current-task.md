# Current task evidence

## Scope
RugBrain public crypto-intelligence audience boundary, canonical 2560-dimensional Qwen3-Embedding-8B path, and default-off public 8B canary selector. The separate 8B re-embedding services and global 8B switch were not modified or restarted.

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
- 8B cutover gate remains closed: the active re-embed process is still processing `codebase_reference_v2` at approximately 290k/2.13M points; `agent_vectors_8b` is 0/141 and multiple targets are incomplete. The new canary selector is default-off and does not perform a global 8B routing, writer, or service cutover.
- Applied reviewed public seed: OpenZeppelin ReentrancyGuard (MIT) and SWC-128 defensive pattern (MIT); `public_crypto_patterns` now has 2 points at 2560 dimensions.
- Public code output sanitizer now removes source-kind labels and public query/projection guards reject prompt-injection instructions; 50 focused tests, Ruff, and targeted mypy passed after the change.
- Full audience-boundary suite after seed/safety changes: `122 passed`; gitleaks clean.
- Live after restart: public code queries return 2/1 approved results with no `contextcrypto` or `source_kind` strings; injection and operational-harm requests return blocked=true; internal collection request returns 403.

## Required command evidence
- `pytest -q tests/test_qdrant_store.py tests/test_qdrant_store_write_gate.py tests/test_content_engine.py tests/test_routes_core.py tests/test_subscriber.py tests/test_main_health.py tests/test_routes_ingest.py tests/test_routes_search.py tests/test_rag_tools_boundary.py tests/test_crypto_code_retrieval.py tests/test_knowledge_boundary.py tests/test_public_projection.py tests/test_public_crypto_code.py tests/test_search_quality.py tests/test_mcp_bridge.py` — executed via `heavy-run pytest`; 120 passed.
- `ruff check src/rugbrain/knowledge_boundary.py src/rugbrain/public_projection.py src/rugbrain/public_crypto_code.py src/rugbrain/routes/search.py src/rugbrain/routes/ingest.py src/rugbrain/routes/core.py src/rugbrain/routes/rag_tools.py src/rugbrain/crypto_code_retrieval.py src/rugbrain/search/engine/knowledge.py scripts/ingest_public_contextcrypto.py tests/test_knowledge_boundary.py tests/test_public_projection.py tests/test_public_crypto_code.py tests/test_routes_ingest.py` — executed via `heavy-run ruff check`; passed.
- `.venv/bin/mypy src/rugbrain/knowledge_boundary.py src/rugbrain/public_projection.py src/rugbrain/public_crypto_code.py src/rugbrain/routes/search.py src/rugbrain/routes/ingest.py src/rugbrain/routes/core.py src/rugbrain/routes/rag_tools.py src/rugbrain/crypto_code_retrieval.py src/rugbrain/search/engine/knowledge.py --ignore-missing-imports` — executed via `heavy-run .venv/bin/mypy`; passed.
- `npx agent-guardrails check --review --commands-run ...` — previously passed with 0 errors and 9 warnings; remaining warnings concern pre-existing/generated files and broad multi-file state changes.

## Default-off 8B canary follow-up
- Implemented public-only canary allowlist and 4096-dimensional validation in `src/rugbrain/canary_routing.py`; internal ContextCrypto and incomplete sources are rejected.
- Wired explicit single-search canary routing in `src/rugbrain/routes/search.py`; multi-search rejects canary sources until grouped routing exists; responses relabel shadow target names and never expose provider/model metadata.
- Added `allow_create=False` Qdrant reads so a missing canary target fails closed and is not auto-created.
- `pytest -q tests/test_canary_routing.py tests/test_routes_search.py tests/test_qdrant_store.py` (executed via `heavy-run pytest`) — 42 passed.
- `pytest -q tests/test_qdrant_store.py tests/test_qdrant_store_write_gate.py tests/test_content_engine.py tests/test_routes_core.py tests/test_subscriber.py tests/test_main_health.py tests/test_routes_ingest.py tests/test_routes_search.py tests/test_rag_tools_boundary.py tests/test_crypto_code_retrieval.py tests/test_knowledge_boundary.py tests/test_public_projection.py tests/test_public_crypto_code.py tests/test_search_quality.py tests/test_mcp_bridge.py` (executed via `heavy-run pytest`) — 125 passed.
- `ruff check src/rugbrain/knowledge_boundary.py src/rugbrain/public_projection.py src/rugbrain/public_crypto_code.py src/rugbrain/routes/search.py src/rugbrain/routes/ingest.py src/rugbrain/routes/core.py src/rugbrain/routes/rag_tools.py src/rugbrain/crypto_code_retrieval.py src/rugbrain/search/engine/knowledge.py scripts/ingest_public_contextcrypto.py tests/test_knowledge_boundary.py tests/test_public_projection.py tests/test_public_crypto_code.py tests/test_routes_ingest.py` (executed via `heavy-run ruff check`) — passed.
- `.venv/bin/mypy src/rugbrain/knowledge_boundary.py src/rugbrain/public_projection.py src/rugbrain/public_crypto_code.py src/rugbrain/routes/search.py src/rugbrain/routes/ingest.py src/rugbrain/routes/core.py src/rugbrain/routes/rag_tools.py src/rugbrain/crypto_code_retrieval.py src/rugbrain/search/engine/knowledge.py --ignore-missing-imports` (executed via `heavy-run .venv/bin/mypy`) — passed.
- `gitleaks detect --no-git --redact --source .` — no leaks found.
- Live opt-in ASGI canary probe: HTTP 200, source collection preserved, 3 results, target/provider leakage false. Live default-off probe: HTTP 200 with canonical behavior preserved.
- `git diff --check` — passed.
- `npx agent-guardrails check --base-ref HEAD~1` with all three required `--commands-run` entries — passed with 0 errors and 2 pre-existing continuity warnings; no production 8B activation or service restart performed.

## Task: canonical content style + Daily Rug long-form lane (2026-10-24)

### Scope
Editorial style contract, free-model prompt pack and the Daily Rug generator.
No change to retrieval, ingest or publication routing.

### Commands and results
- `python3 -m pytest tests/test_content_style.py tests/test_daily_rug.py tests/test_content_engine.py tests/test_content_gate.py tests/test_content_candidates_pool.py tests/test_factcheck.py -q` — 102 passed.
- `python3 -m ruff check` on all touched source and test files — passed.
- `python3 -m mypy` on the new modules — no errors in the touched files (31 pre-existing errors reported in imported modules).
- `python3 scripts/check-structure.py --repo .` — no findings in new files; `content_engine.py` 341 -> 338 lines (shrank).
- `python3 scripts/check-antipatterns.py --repo .` — no findings in touched files.

### Changes
- `config/content_style.json` is the single style source; `config/content_prompts.json` the single prompt source.
- `rugbrain.content_style`, `.content_style_normalise`, `.content_style_lint`, `.content_prompts`.
- `rugbrain.daily_rug` plus evidence, refs, render, meta and validate modules.
- `content_engine.py` now renders its Telegram writer prompt from the canonical pack.

### Residual risk
- Repo-wide `check-monkeypatch.py` and pre-existing structure ratchets fail on files untouched by this task (baseline drift at branch point).
- `search_multi_collection` defaults are exercised through an injected search function in tests; no live Qdrant call was made.
