# Collection prune — inventory, decisions, guard (2026-10-25)

Measured against live Qdrant (`127.0.0.1:6334`, container `rmi-qdrant-v2`): 92
collections. Consumer = an exact name reference in `src/`, `scripts/`, `config/`,
`docs/`, `tests/`, or the `_8b` twin of a base the lane table routes
(`lane_twin()` derives the name at runtime, so a plain grep reports ~40 false
orphans).

## Rules (applied in order)

| # | verdict | condition | action |
|---|---|---|---|
| R1 | **retire** | 0 points, no consumer, no twin | delete (script, dry-run first) |
| R2 | **redirect** | 0 points, twin populated | alias twin over the base name |
| R3 | **fixture** | test/scratch name, or referenced only from `tests/` | move out of the production namespace, document |
| R4 | **rebuild** | points > 0 at a non-canonical dim | re-ingest on the 4096 lane |
| R5 | **keep** | points > 0 and a consumer | nothing |
| R6 | **orphan** | points > 0 and no consumer | **alarm**, operator decides; never auto-deleted |

## Decisions

| collection | pts | dim | twin pts | consumer | verdict |
|---|---:|---:|---:|---|---|
| `live_threats` | 0 | 4096 | 11,164 | `alert_routes`, `lane_table`, `namespace`, `owned_registry` | **redirect** → `live_threats_8b` |
| `exploits` | 0 | 4096 | 1,344 | lane table (declared, not owned) | **redirect** → `exploits_8b` |
| `cross_chain` | 0 | 4096 | 13 | `entries_protocol`, `owned_registry`, lane table | **redirect** → `cross_chain_8b` `[REVIEW]` naming conflict |
| `regulation_enforcement` | 0 | **2560** | 5,559 | `firehose/market_sources`, `ingest_sources/opensanctions*` | **redirect** → `regulation_enforcement_8b` |
| `crypto_sector_docs` | 0 | 4096 | 0 | none anywhere | **retire** |
| `mem0migrations` | 0 | 4096 | 0 | `corpus/entries_runtime` (declared entry) | **retire** (drop the entry) — sign-off |
| `scan_intel` | 20,052 | **2560** | 68,915 | `routes/intel`, `lane_table`, `owned_registry` | **rebuild** (migrate, then retire base) — sign-off |
| `news_v2` | 24 | 4096 | 299,074 | `probes` (CANONICAL), `fear_index`, `routes/intel`, 20 tests | **redirect?** — `[REVIEW]` schema differs (story-level vs chunk-level); needs sign-off |
| `dedup_slice_test` | 316 | 4096 | – | none | **fixture** |
| `security_patterns_test` (+`_8b`, `_hybrid`) | 72/93/92 | 4096 | – | **`src/qdrant_store/lane_table.py`, `knowledge_boundary_tables`, `corpus/entries_threat`** | **fixture leaking into prod registry** — remove from registry |

## Fixture leak (worth fixing first)

`security_patterns_test` is referenced from production modules, not only tests.
A test fixture in the lane table is a live name with 72 points that the resolver
may route to. It should be prefixed `test_` and excluded by the guard.

## Guard

`tests/test_collection_registry_guard.py` fails when an advertised name holds 0
points, and when a NEW orphan (points > 0, no consumer) appears.
`config/collection_orphans_allow.json` is the ratchet baseline: it may shrink,
never grow. Skips when Qdrant is unreachable rather than passing silently.

## Still needs operator sign-off

1. Deleting the 2 empty retires (`crypto_sector_docs`, `mem0migrations`).
2. `--apply` on the alias redirects (drops 0-point bases, no data touched).
3. Retiring the 2560 bases `scan_intel` and `regulation_enforcement` *after*
   confirming twin coverage.
4. `news_v2` — payload schema differs from its twin; aliasing changes what
   `/api/v1` news readers return.
