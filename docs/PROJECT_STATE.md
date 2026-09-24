# Project State

Last updated: 2026-09-24

## Goal

Keep RugBrain as the public crypto-intelligence and entity layer, while ContextCrypto remains the private code/security/engineering knowledge tool. The public surface must answer with curated evidence without exposing internal code, prompts, paths, model/provider identity, or detection methodology.

## Current focus

Enforce the public/internal audience boundary in search, coding enrichment, ingestion, and response serialization. Preserve the 2560-dim canonical path and do not cut over to the separate 8B corpus until its migration gates pass.

## Done recently

- Added dimension and vector-name validation to Qdrant collection reads and writes.
- Added dynamic dense/sparse vector-name discovery for hybrid collections.
- Removed the retired 1024-dimensional startup collection bootstrap.
- Added schema-aware health/readiness checks and an additive `/embed` compatibility route.
- Normalized provider embedding URLs and stopped `/embed` responses from exposing provider identity.
- Added `knowledge_boundary.py` as an explicit public collection allowlist and internal collection classifier.
- Public search/ingestion now reject internal ContextCrypto collection names.
- Public search responses recursively remove source paths, prompts, model/provider fields, raw payloads, and graph coordinates.
- Raw ContextCrypto code-mode retrieval is hidden behind an operator-only opt-in scope; public coding search uses curated RugBrain knowledge by default.
- Added the reviewed `public_crypto_patterns` projection contract and a dry-run-first JSONL importer; raw ContextCrypto collections are never read by the importer. Public code projections may include reviewed open-source library snippets and defensive exploit knowledge, never internal apps/repos.
- Public crypto-code search is served only from the projection and refuses requests to weaponize or execute attacks. Approved public/open-source snippets may be returned with public URLs/licenses; internal apps/repos remain unavailable.
- Applied a reviewed two-record public seed (OpenZeppelin ReentrancyGuard and SWC-128); future additions must pass the same projection, license, prompt-injection, and leak checks.
- Added audience-boundary, route, ingest, code-mode, and projection regression coverage.
- Accepted the audience/projection rules in `docs/RAG-MCP-BOUNDARY.md` and `docs/DATA-PLACEMENT.md`.

## Blockers

- Full-repository mypy and Ruff gates retain unrelated baseline failures.
- Retired empty 1024-dimensional Qdrant placeholders require a separate migration decision.
- The 8B corpus is still incomplete; no reader/writer cutover is allowed.
- PublicContextCrypto projections contain only the reviewed seed; expansion remains gated and raw ContextCrypto collections must remain internal.

## Next step

Run focused tests/lint/type checks, review the audience-boundary diff, commit and push only intended RugBrain changes, then perform non-secret public-route smoke checks without touching the re-embedding service.
