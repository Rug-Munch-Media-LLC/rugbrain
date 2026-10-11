# RMI RAG — Crypto Security Data Strategy

> ⚠️ **STACK CORRECTION (2026-09-20):** the embedding stack described below as
> "BGE-M3 via Ollama" is **obsolete**. The live stack — identical to
> rmi-codebase-mcp — is **`Qwen/Qwen3-Embedding-8B` (2560d)**: DeepInfra
> (canonical primary) + local llama.cpp `:8004` (same model, index-compatible
> fallback). Reranking is **live** (`Qwen/Qwen3-Reranker-8B`, local `:8003` +
> DeepInfra hosted); the 0.6B rerank tier retired 2026-09-14. Ollama is not in
> the embed/rerank path. Wherever this doc says "BGE-M3" or "Ollama
> `/api/embed`", read Qwen3-Embedding-8B via llama.cpp/DeepInfra.


**Status:** implementation spec (v1) · **Owner:** crmuncher · **Date:** 2026-08-01 · **Repo:** `RugMunchMedia/rugbrain`
**Scope:** pure design/planning. This file is the contract implementing agents follow for data ingestion. No code was changed to produce it.

> **How to use this document**
> - Sections 1–11 are normative. Agents MUST NOT deviate without a written note in the PR.
> - Facts marked `[verified]` were confirmed against the live systems on 2026-08-01. Facts marked `[decision]` fill gaps in the original brief and are binding unless the reviewer objects.
> - Section 12 is the build order and owner per step. Section 13 is the acceptance test every step must pass.
> - Source URLs marked "verify at implementation" were NOT confirmed on 2026-08-01 (dead or unprobed endpoints). Agents must confirm the URL and record it in the ingest script header before relying on it.

---

## 0. Target State (one picture)

```
                    ┌─────────────────────────────────────────────┐
                    │            ingest sources (free)            │
                    │  DeFiHackLabs · SWC · Rekt · SlowMist ·     │
                    │  ScamSniffer · MetaMask · Immunefi · …      │
                    └──────────────────────┬──────────────────────┘
                                           ▼
                    ┌─────────────────────────────────────────────┐
                    │  scripts/ingest_*.py  (idempotent, flocked) │
                    │  ─────────────────────────────────────────  │
                    │  INDICATOR layer (sparse-only, ~113K docs)  │
                    │    scam_intel: domains + addresses          │
                    │  KNOWLEDGE layer (dense+sparse, ~2K docs)   │
                    │    threat_intel: SWC + PoC patterns         │
                    │    hacks: historical exploit registry       │
                    └──────────────────────┬──────────────────────┘
                                           ▼
                    ┌─────────────────────────────────────────────┐
                    │  Qdrant (rmi-qdrant:6333) · Redis (indicator │
                    │  sets + doc cache) · Qwen3-Embedding-8B 2560d │
                    └──────────────────────┬──────────────────────┘
                                           ▼
                    ┌─────────────────────────────────────────────┐
                    │  B2 permanence (nightly, encrypted)          │
                    │  rmi-rag-permanence/snapshots/YYYY-MM-DD/   │
                    └─────────────────────────────────────────────┘
```

**Non-negotiables** (inherited from ARCHITECTURE-V2.md, binding):
1. ONE embedding stack: Qwen3-Embedding-8B (2560d) — llama.cpp `:8004` + DeepInfra (same model). No `sentence-transformers`/`torch` in any ingest path (the local model in `scripts/ingest_threats.py` is the anti-pattern; that script is superseded).
2. ONE store: Qdrant. No ChromaDB, no numpy ANN, no Redis vectors.
3. ONE search path: dense + keyword + RRF (`retrieval.py` per v2). The fake `splade_bm25.py` and fake `cross_encoder.py` must NOT be extended; they are slated for deletion by v2.
4. Idempotent by construction: deterministic UUIDv5 doc IDs + `content_hash`; reruns insert nothing new.
5. Fail-soft: a dead source never fails the run — staged cache fallback (existing `SEED_DATA_DIR` pattern), zero points ingested from that source, run exits 0 with a warning.
6. No secrets in git. All credentials via env / the secrets manager (fleet standard).

---

## 1. Guiding Principles (why this plan is lean)

The original brief targets ~130K documents. The single most important design decision is **separating what needs an embedding from what does not**:

| Layer | What | Size | Embedding needed? |
|---|---|---|---|
| **Indicators** | phishing domains, scam addresses (exact-match keys) | ~113K | **NO** — cosine similarity between two domains is noise. Keyword (sparse) vector only, ~1–5 non-zero terms each. |
| **Knowledge** | SWC weaknesses, PoC writeups, hack reports, patterns | ~2K | **YES** — 2560d Qwen3-Embedding-8B dense + BM25 sparse |

Embedding math (measured 2026-07-31 calibration, 4-CPU Ollama, qwen3-embedding-8b):
- Short text (≤600 chars): batch 20 → ~3.5s/doc
- Long text: batch 8
- **~2,000 knowledge docs ≈ 1–2 hours one-time.** Ongoing (weekly) adds are tens of docs.
- ~113K indicators are encoded locally as BM25 sparse vectors (CPU-only, no Ollama call) in well under a minute.

This is what keeps "most knowledgeable in existence" and "doesn't bog down the server" simultaneously true: **the 98% that is lookup data never touches the embedder.**

---

## 2. Data Categories & Collection Map

| Collection | Purpose | Layer | Status | Contents (target) |
|---|---|---|---|---|
| `threat_intel` | Vulnerability patterns & techniques | Knowledge | EXISTS (26 docs) — extend + rebuild with named vectors | SWC Registry (~37), DeFiHackLabs PoC summaries (~200–400), curated patterns (~30) |
| `scam_intel` | Known scams & malicious indicators | Indicator + patterns | EXISTS (600 docs) — rebuild full, absorb `scams` | MetaMask domains (~107K), ScamSniffer addresses (~2.5K), Etherscan phish labels, ChainAbuse/CryptoScamDB (P1), honeypot/rug/drainer patterns (~10) |
| `hacks` | Historical DeFi exploit registry | Knowledge | **NEW — create** | Rekt leaderboard (~310 + article summaries), SlowMist blockchainSecurityDB (~100+) + Medium weekly reports, DeFiHackLabs incidents, Immunefi disclosures (P1) |
| `news` | Real-time market awareness | Knowledge | EXISTS (623 docs) — unchanged | RSS pipeline (already wired, 15-min) |
| `wallet` | Entity labels | Indicators | EXISTS (1000 docs) — unchanged | Redis `rmi:label:*` → address→label/entity resolution |

**Legacy cleanup `[decision]`:**
- `scams` (300-doc MetaMask *sample*) is superseded by the full blacklist in `scam_intel`. Migration: seed `scam_intel` with the full MetaMask list, then delete `scams` (verify zero production callers first — grep for `collection="scams"` / `'scams'` in `src/` and `scripts/`; `seed_qdrant_collections.py` `load_scams_from_metamask` and its `COLLECTION_LOADERS` entry are removed in the same commit).
- `scanner`, `research`, `rmi_agent_memory`, `rmi_community` are out of scope here and unchanged.

---

## 3. Data Sources

### 3.1 Verified P0 sources

| Source | Access | What we ingest | Notes (verified 2026-08-01) |
|---|---|---|---|
| **DeFiHackLabs** (`SunWeb3Sec/DeFiHackLabs`) | shallow git clone | Solidity PoCs + incident summaries | Repo layout confirmed: `src/test/<YYYY-MM>/` per-incident PoCs, `past/<YYYY>/` archives, README index. **Use shallow clone, NOT GitHub API** (60 req/hr unauth). |
| **SWC Registry** (`SmartContractSecurity/SWC-registry`) | shallow git clone | Per-weakness markdown (SWC-101…) | Weakness entries under `entries/` (layout confirmed at repo root; exact sub-path enumerated at implementation via `git ls-tree`). |
| **Rekt News** (`rekt.news/leaderboard`) | HTTP scrape | ~310 leaderboard rows + per-article summaries | **No public JSON API** — `leaderboard.json` and `/rss.xml` both return 500 `[verified]`. Leaderboard HTML is static: each row = name, amount (`$14,847,374,246`), date (`12/20/2020`), slug (`/cream-rekt-2`). Scrape leaderboard, then fetch top-N article pages for root-cause text. |
| **SlowMist** | GitHub + RSS | Structured per-project findings + weekly hacked reports | **No `slowmist/Hacked-Archive` or `CryptoHackLabs` repo — both 404 `[verified]`.** Use `slowmist/blockchainSecurityDB` (confirmed: `projects/<slug>.json` per project, structured) + SlowMist Medium RSS `medium.com/feed/@SlowMist` (weekly "hacked" reports). Verify both URLs at implementation. |
| **ScamSniffer** (`scamsniffer/scam-database`) | raw.githubusercontent | Phishing addresses | Already wired in `seed_qdrant_collections.py` (`blacklist/address.json`). |
| **MetaMask eth-phishing-detect** (`MetaMask/eth-phishing-detect`) | raw.githubusercontent | Phishing domains (~107K) | Already wired (`src/config.json` → `blacklist` array). Current seed samples 300 — this strategy loads **the full list**. |

### 3.2 P1/P2 sources (verify at implementation — fail-soft if unreachable)

| Source | Access | Items | Priority | Notes |
|---|---|---|---|---|
| **Immunefi** | RSS + public disclosures | 100+ | P1 | Verify feed URL; best-effort scrape of disclosed reports with 1 req/s politeness. |
| **ChainAbuse** | API/RSS | 10K+ | P1 | Verify endpoint; address+category records → `scam_intel` indicators. |
| **CryptoScamDB** | API | 5K+ | P1 | Verify endpoint; domain/address + scam type. |
| **PeckShield** | Blog/Twitter/Medium RSS | 100+ | P1 | Medium feed preferred (RSS). |
| **BlockSec** | Blog | 50+ | P2 | blocksecteam.com blog feed. |
| **CertiK** | Alert feed | ongoing | P2 | skynet/alerts feed; low cadence. |

**Source-verification gate:** every ingest script opens with a `SOURCES` docstring listing exact URLs + fetch method, and must include a `--check-sources` mode that prints HTTP status for each URL. A P1/P2 source that 404s logs a warning and contributes zero docs — it never blocks the run.

---

## 4. Two-Layer Ingestion Model

### 4.1 Indicator layer (`scam_intel`) — sparse-vector only
- Doc per domain/address: content = `Phishing domain: <domain>` / `Scam address <addr> on <chain>` (existing format).
- Vector: **BM25 sparse only** (`sparse` named vector, 1–5 terms). No dense vector → these points never appear in semantic results; they surface via keyword search + payload filters and via Redis exact-match.
- **Redis mirror (authoritative for lookups):** after upsert, `SADD rag:indicator:phish_domain <domain>` and `rag:indicator:scam_address:{chain} <addr>`. The scam-check path (`scam_detection.py`) does an O(1) `SISMEMBER` before any vector search. Redis sets for 113K entries ≈ 5–10MB — negligible.
- Dedup: `doc_id = scam_intel:indicator:domain:{domain}` / `scam_intel:indicator:address:{chain}:{addr}` (UUIDv5).

### 4.2 Knowledge layer (`threat_intel`, `hacks`) — dense + sparse
- Doc per weakness / PoC / hack report / pattern.
- Vector: dense 2560d Qwen3-Embedding-8B (L2-normalized, llama.cpp `:8004` / DeepInfra, existing batch helper `_embed_batch`: 20 short / 8 long) **+** BM25 sparse.
- Chunking: reuse `_chunk_text` (2000 chars, 100 overlap) from `seed_qdrant_collections.py` for articles >2000 chars; Solidity PoCs use `src/rugbrain/solidity_chunker.py`. Chunks share `parent_doc_id` + `chunk_index` in payload.

---

## 5. Ingestion Strategy

### Phase 0 — Prerequisites (1 script + infra, owner: data-infra-lead, S)
1. Add deps to `requirements.txt` / `pyproject.toml`: `zstandard` (zstd-19), `cryptography` (AES-GCM), `boto3` (already used by `backup_qdrant.py`, missing from manifest).
2. Create `hacks` collection with named vectors (`dense` 1024 Cosine + `sparse` BM25) + payload indexes (§6.4). Create rebuilt `threat_intel` + `scam_intel` under temp names; swap aliases after backfill (§6.5).
3. `scripts/sparse_encoder.py` — the BM25 contract (§8.1).
4. Write `scripts/lib/ingest_common.py` `[decision]`: shared helpers — fetch-with-cache (existing `SEED_DATA_DIR` pattern), `_embed_batch` (copy from seed script), `_to_uuid`, `content_hash` (sha256 of normalized content), `upsert_points` (REST or qdrant-client), flock guard, per-run counters to stdout JSON.

### Phase 1 — Batch seed (one-time; ~2h total embed)
| Script | Source → Collection | Effort | Notes |
|---|---|---|---|
| `scripts/ingest_swc_registry.py` | SWC → `threat_intel` | S | One doc per weakness; payload: `cve=null`, `attack_vector` mapped from SWC category, `source_type=swc` |
| `scripts/ingest_defihacklabs.py` | DeFiHackLabs → `threat_intel` + `hacks` | M | PoC summary → `threat_intel` (pattern knowledge); incident record → `hacks` (with tx hash/amount if present in README/index). Parse README index for incident metadata; fall back to directory name. |
| `scripts/ingest_rekt_news.py` | Rekt → `hacks` | M | Scrape leaderboard (310 rows: name, amount, date, slug) → `hacks:rekt:{slug}`; fetch article pages for top-150 by amount → append root-cause/vector text to same doc (chunked). |
| `scripts/ingest_slowmist.py` | blockchainSecurityDB + Medium RSS → `hacks` | M | One doc per `projects/<slug>.json` finding; weekly Medium reports appended as dated docs. |
| `scripts/ingest_scam_indicators.py` | ScamSniffer + MetaMask (+ Etherscan CSV) → `scam_intel` | M | Loads FULL MetaMask blacklist (~107K) + ScamSniffer addresses; sparse-only; Redis mirror. Replaces the 300-sample path in `seed_qdrant_collections.py`. |

All scripts: `--dry-run`, `--limit`, `--source <name>` flags; idempotent (skip existing UUIDv5 IDs); require `flock` (see §11).

### Phase 2 — Continuous updates (cheap, scheduled)
| Cadence | Job | What |
|---|---|---|
| Daily 04:00 | `ingest_scam_indicators.py` (existing MetaMask/ScamSniffer fetch) | Diff: new domains/addresses upserted; **entries no longer in the source snapshot are deleted** (list is a full snapshot — stale entries are false positives). Redis sets rebuilt in place. |
| 15-min | `rmi-news-ingest` (exists) | News RSS → `news`. Do not touch beyond adding the flock fix (TODO). |
| Weekly Mon 03:00 | `rugbrain-ingest` (exists, repointed to new scripts) | Rekt leaderboard refresh (new entries), SlowMist blockchainSecurityDB pull, DeFiHackLabs `git pull`, Immunefi best-effort. |
| Weekly Mon 03:30 | `rmi-rag-permanence-restore` | Restore test to a scratch collection → verify counts → drop (fleet standard). |

---

## 6. Qdrant Collection Schema

### 6.1 Vector config (knowledge collections: `hacks`, `threat_intel`, rebuilt `scam_intel`)
```json
{
  "vectors": {
    "dense":  { "size": 1024, "distance": "Cosine" }
  },
  "sparse_vectors": {
    "sparse": {}
  },
  "hnsw_config": { "m": 16, "ef_construct": 100 },
  "optimizers_config": { "default_segment_number": 2 }
}
```
`news`, `wallet`, `scanner`, `research` keep their existing single-vector config (no rebuild — keyword filtering there is via payload filters, per v2).

### 6.2 Payload schema (unified; all collections)
System fields (all collections):

| Field | Type | Required | Notes |
|---|---|---|---|
| `content` | string | yes | truncated at 10,000 (existing store contract) |
| `doc_id` | string | yes | human-readable id (see §6.3) |
| `content_hash` | string | yes | sha256 of normalized content → dedup key |
| `schema_version` | int | yes | `1` for this strategy |
| `ingested_at` | ISO-8601 | yes | |
| `embedding_model` | string | yes | `qwen3-embedding-8b` |
| `source` | string | yes | `rekt_news`, `defihacklabs`, `swc`, `slowmist`, `scamsniffer`, `metamask`, … |
| `source_url` | string | no | |
| `collection` | string | yes | existing convention |

Knowledge fields (`hacks`, `threat_intel`; example = the brief's Cream Finance record):

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | "Reentrancy Attack on Cream Finance" |
| `category` | string | yes | `pattern` / `weakness` / `hack` / `actor` / `scam` |
| `attack_vector` | string | yes | enum §7.1 |
| `severity` | string | yes | `critical`/`high`/`medium`/`low`/`info` |
| `ecosystem` | string | yes | enum §7.2 |
| `chain` | string | yes | `ethereum`, `solana`, `bsc`, `polygon`, `arbitrum`, `base`, `multi`, `web`, … |
| `date` | ISO-8601 (date) | no | `2021-10-27` — range filterable |
| `amount_lost_usd` | float | no | `130000000` — range filterable |
| `protocol` | string | no | "Cream Finance" |
| `cve` | string\|null | no | |
| `tags` | string[] | no | `["reentrancy","erc777","lending","flash-loan"]` |
| `tx_hash` | string | no | on-chain evidence |
| `post_mortem_urls` | string[] | no | |
| `parent_doc_id` + `chunk_index` | string, int | no | chunked docs only |

Indicator fields (`scam_intel`): `address` or `domain`, `category` (`phishing`/`honeypot`/`rugpull`/`drainer`/…), `label`, `date_added`.

### 6.3 Doc-ID scheme (deterministic, UUIDv5 via `QdrantVectorStore._to_uuid`)
```
hacks:rekt:{slug}                 hacks:defihacklabs:{incident}
hacks:slowmist:{slug}             hacks:immunefi:{id}
threat_intel:swc:{swc_id}         threat_intel:defihacklabs:{incident}:{chunk}
threat_intel:pattern:{name}       scam_intel:indicator:domain:{domain}
scam_intel:indicator:address:{chain}:{addr}   scam_intel:pattern:{name}
```

### 6.4 Payload indexes (create on all collections that have the field)
`date` (range) · `amount_lost_usd` (range) · `ingested_at` (range) · `severity` (keyword) · `category` (keyword) · `attack_vector` (keyword) · `ecosystem` (keyword) · `chain` (keyword) · `protocol` (keyword) · `source_type` (keyword) · `content_hash` (keyword).

This makes the canonical filtered query — *"all reentrancy attacks on lending protocols over $10M"* — a payload-filtered dense+sparse search, no scan.

### 6.5 Migration (critical — Qdrant vector config is immutable)
Existing `threat_intel` / `scam_intel` were created with an unnamed single vector; sparse vectors cannot be added in place. Procedure:
1. Create `threat_intel_v2` / `scam_intel_v2` with §6.1 config + indexes.
2. Backfill from latest B2/R2 snapshot (or reseed via the Phase-1 scripts — they are the same code path).
3. `PUT /collections/aliases` → point `threat_intel` → `threat_intel_v2` (alias swap, zero downtime).
4. Delete old physical collection. Same for `scam_intel`; `hacks` is created fresh with the right config — no migration.

---

## 7. Metadata Taxonomies (shared enums — aligned with rmi-codebase MCP)

### 7.1 `attack_vector` (14 values, canonical)
`reentrancy` · `flash_loan` · `oracle_manipulation` · `signature_malleability` · `mev` · `bridge_exploit` · `access_control` · `input_validation` · `business_logic` · `storage_collision` · `zk_underconstrained` · `supply_chain` · `cryptographic_failure` · `timing` · (plus `unknown`/`multiple`). Every knowledge doc MUST map to exactly one primary value; additional vectors go in `tags`.

### 7.2 `ecosystem`
`EVM` · `Solana` · `Cosmos` · `ZK` · `AI` · `Cross-Chain` · `General` (mirrors rmi-codebase).

### 7.3 `severity` rubric (for source data with no rating)
- `critical`: ≥$10M lost, or theft of funds / mint of value, or exploits live today
- `high`: ≥$1M, or a live scam pattern (honeypot, drainer)
- `medium`: informational pattern, <$1M
- `info`: news, research, entity profiles

---

## 8. Search & Retrieval

### 8.1 BM25 sparse encoder — `scripts/sparse_encoder.py` `[decision]`
Contract (implementer's choice of internals; must satisfy):
- Input: text → output: `{"indices": [...], "values": [...]}` (top-128 terms by weight).
- Tokenizer: lowercase, alphanumeric only, drop stopwords (`the`, `and`, …), min token length 3.
- Weights: BM25 term frequency × IDF. IDF from **per-collection document frequencies** — computed once by scanning the collection and cached to `SEED_DATA_DIR/bm25_idf_{collection}.json`, refreshed weekly with the knowledge refresh.
- Deterministic: same text → same sparse vector (tested).
- Never calls Ollama.

### 8.2 Retrieval path (per v2: dense + keyword + RRF)
1. Dense: `dense` vector search on Qwen3-Embedding-8B query embedding, top-K (K=64).
2. Sparse: `sparse` vector search (Qdrant native sparse index) with the same query, top-K.
3. Fusion: Reciprocal Rank Fusion (k=60 — reuse logic from `rrf_fusion.py`, folded into `retrieval.py` per v2) → top-N.
4. Filtering: payload filters from structured query (severity, attack_vector, amount_lost_usd range, date range, chain, ecosystem, protocol) applied as `must` clauses (existing `search/filters.py` pattern).
5. Optional stage: LLM-based re-rank on top-5 for chat answers (already possible via API); no local cross-encoder.

### 8.3 Reranker (P2, optional, `[decision]`)
The existing `cross_encoder.py` is a fake (bi-encoder cosine) and must NOT be used. **Status 2026-09-20: a real reranker is LIVE** — `Qwen/Qwen3-Reranker-8B` (Q8_0) self-hosted via llama.cpp `:8003` (`qwen-reranker.service`) plus the DeepInfra hosted lane (same model), wired through `reranker.py` + `rerank_hosted.py`. The 0.6B rerank tier retired 2026-09-14. RRF + payload filters remain the baseline; the reranker is the quality lane on top.

---

## 9. Data Lifecycle (reconciled with actual infra)

The brief's hot/warm/cold tiers are reframed to match what this stack actually is (v2: one store, no Redis ANN):

| Tier | What | Where | Size budget |
|---|---|---|---|
| **Hot** | All knowledge collections + indicators (they all fit) | Qdrant: dense HNSW in-RAM, payload on-disk; Redis indicator sets + doc cache | ~10K points in Qdrant (≈40MB dense RAM); ~113K keys in Redis (≈10MB) |
| **Warm** | Exact-match indicators + recent hot docs cache | Redis (`rag:indicator:*`, `rag:{collection}:{id}` existing pattern) | as above |
| **Cold** | Everything, forever | B2 permanence (§10) + existing Qdrant API snapshots → R2 (fast restore path) | nightly |

**Pruning policy:**
- **News/scanner (volatile):** delete points older than 90 days from Qdrant (scroll by `date`/`ingested_at` filter → `delete`). Critical/high-severity news items are exempt. Qdrant `collection/points/delete` with a `must` date-range filter — one call, no scan in Python.
- **Hacks/threat_intel:** **permanent by design** — a historical exploit registry never ages out. No age pruning. Dedup only.
- **Indicators:** replace-on-update (daily snapshot diff, §5 Phase 2). Stale entries deleted, not kept.
- **Dedup (all collections):** `content_hash` collision → keep newest `ingested_at` (upsert with the deterministic doc_id already overwrites; `content_hash` makes the conflict explicit and auditable).

**Capacity note:** at the §0 sizes the Qdrant hot tier is ~10K points ≈ well inside a lean 4GB host. No cold eviction of knowledge is ever needed; the tier machinery exists for `news`/`scanner` hygiene and disaster recovery, not for capacity pressure.

---

## 10. B2 Permanence

### 10.1 Format (portable, human-readable, restorable)
```
rmi-rag-permanence/
├── snapshots/
│   └── 2026-08-01/
│       ├── threat_intel.jsonl.zst.enc
│       ├── scam_intel.jsonl.zst.enc
│       ├── hacks.jsonl.zst.enc
│       └── news.jsonl.zst.enc          # wallet/scanner/research/… as present
├── monthly/
│   └── 2026-07.jsonl.zst.enc.tar.gz    # aggregate
└── manifest.json
```
- **NDJSON lines** (one doc per line): `{"doc_id","content","metadata","severity","chain","source","dense":[...],"sparse":{"indices":[...],"values":[...]},"content_hash","ingested_at","schema_version"}`
- **Compression:** zstd level 19 (`zstandard`).
- **Encryption:** AES-256-GCM, **key derivation identical to walletpress** `[verified 2026-08-01]`: Argon2id `time_cost=3, memory_cost=65536, parallelism=4, salt=16B` → AESGCM 256, `nonce=12B`; file format `base64(salt ‖ nonce ‖ ciphertext)`. Salt + nonce random per file.
- **Key:** `RAG_PERMANENCE_KEY` via env (from the secrets manager on the host; never in git, never in `.env` in the repo).
- **Manifest:** per-file `sha256`, doc counts, `schema_version`, snapshot time, collection list. `manifest.json` is written last and is the restore source of truth.

### 10.2 Retention
Daily × 7 · weekly (last of each week) × 4 · monthly × forever. Enforced by a lifecycle sweep inside the permanence script (list → classify by age → delete expired objects). Existing `backup_qdrant.py` (Qdrant API snapshots → R2) continues as the fast full-restore path; B2 permanence is the portable, encrypted, forever archive.

### 10.3 Restore & verification
- `scripts/permanence.py --restore --date 2026-08-01` → download → verify sha256 → decrypt → decompress → upsert to Qdrant (idempotent via UUIDv5). A `--verify-only` mode downloads + decrypts + checksums without touching Qdrant.
- Weekly restore test to scratch collection `hacks_restore_test` → count match → drop (fleet standard: `backup-restore-test.sh`).
- **Nightly 03:30 UTC** timer (after the 03:00 Qdrant backup), `flock`ed.
- Depends on fixing the known blocker: `backup_qdrant.py` currently fails nightly ("Credential access key has length 0" — TODO MEDIUM). B2/S3 creds must be wired via rclone / the secrets manager before the permanence timer is enabled.

---

## 11. Scheduling (systemd)

| Timer | Cadence | Job | Lock |
|---|---|---|---|
| `rmi-news-ingest.timer` (exists) | 15-min | news RSS → `news` | flock (fix the pileup — TODO HIGH) |
| `rmi-rag-permanence.timer` (new) | daily 03:30 | §10 snapshots → B2 + lifecycle sweep | flock |
| `rmi-scam-refresh.timer` (new) | daily 04:00 | `ingest_scam_indicators.py` diff | flock |
| `rugbrain-ingest.timer` (exists, repoint) | Mon 03:00 | knowledge refresh (Rekt, SlowMist, DeFiHackLabs, Immunefi) | flock |

All ingest services: `Type=oneshot`, `TimeoutStartSec` generous, `ExecStart` wrapped in `flock -n /run/rugbrain-{job}.lock` — a missed run is better than two overlapping runs saturating Ollama (the exact failure mode documented in TODO).

---

## 12. Implementation Plan

| # | Step | Agent | Effort | Depends on | Acceptance |
|---|---|---|---|---|---|
| 0 | Add deps (`zstandard`, `cryptography`, `boto3`), `sparse_encoder.py`, `ingest_common.py`, create `hacks` + rebuild threat_intel/scam_intel with named vectors + indexes | data-infra-lead | M | — | §13 checks 1, 2, 8 |
| 1 | `ingest_swc_registry.py` | junior-dev | S | 0 | §13 check 3 (SWC row) |
| 2 | `ingest_defihacklabs.py` | junior-dev | M | 0 | §13 checks 3, 4 |
| 3 | `ingest_rekt_news.py` | junior-dev | M | 0 | §13 checks 3, 5 (Cream ≥$10M query) |
| 4 | `ingest_slowmist.py` | junior-dev | M | 0 | §13 check 3 |
| 5 | `ingest_scam_indicators.py` + Redis mirror + `scams` migration | scanner-engineer | M | 0 | §13 checks 6, 7 |
| 6 | BM25 sparse + RRF in retrieval path (fold into v2 `retrieval.py`; remove fake splade/cross-encoder usage) | data-pipeline | M | 0, ARCHITECTURE-V2 landing | §13 checks 5, 8 |
| 7 | B2 permanence pipeline (`permanence.py` v2: NDJSON+zstd+encrypt+manifest+restore+retention) + timer + creds fix | devops-sre | M | 0; backup creds | §13 checks 9, 10 |
| 8 | Phase-2 schedules (scam daily timer, knowledge weekly repoint, flock on news ingest) | devops-sre | S | 2–7 | §13 check 11 |

Total: ~8 steps, all S–M. No new services. No new MCPs.

---

## 13. Verification & Acceptance

1. `hacks` collection exists with named `dense`+`sparse` vectors, 2560-dim Cosine, payload indexes present (`GET /collections/hacks`).
2. Rebuild scripts are idempotent: run twice → second run inserts 0 (log shows `all N docs already present`).
3. `hacks` ≥ 300 docs; `threat_intel` ≥ 40 docs; `scam_intel` ≥ 100K docs (full MetaMask list, not the 300 sample).
4. DeFiHackLabs seed: ≥1 doc whose `attack_vector` matches its SWC-classified vector, and `source_url` points at the repo.
5. Golden query (via API, dense+sparse+filter): *"reentrancy attacks on lending protocols over $10M"* returns Cream Finance (2021-10-27, $130M) in top-5, with `severity=critical`.
6. Redis `SISMEMBER rag:indicator:phish_domain` returns true for a known MetaMask-listed domain; `scam_intel` count grows and stale entries are removed after the daily diff.
7. `scams` collection deleted; zero `collection="scams"` references remain in `src/` + `scripts/`.
8. Sparse-only points never appear in dense search results (indicator isolation test).
9. B2 snapshot for a given date downloads, decrypts, and checksums clean (`--verify-only`); restore to scratch collection matches doc counts.
10. Retention sweep leaves 7 daily + 4 weekly + monthly(s) and removes older objects.
11. No overlapping runs: with an artificial slow embed, the timer's second tick exits immediately via flock (no process pileup).
12. `gitleaks detect --no-git` clean; no API keys or the permanence key appear in the repo.

---

## 14. Guardrails & Failure Modes

| Failure | Behavior (mandatory) |
|---|---|
| Source fetch 404/timeout | Fail-soft: staged cache fallback; 0 docs from that source; exit 0 with warning |
| Ollama down / embed timeout | Skip batch, log, exit non-zero but **no partial payload corruption** (batch-level skip, existing pattern); next run retries idempotently |
| GitHub API rate limit | Never used for bulk: shallow `git clone`/`git pull` + `raw.githubusercontent.com` instead |
| Ollama OOM | Batch sizes stay at 20 short / 8 long; single qwen3-embedding-8b model; never run local `sentence-transformers` |
| Timer overlap | `flock -n` everywhere |
| Duplicate/updated content | `content_hash` + deterministic doc_id upsert (newest wins) |
| Permanence key missing | Script refuses to run (fail-closed) and alerts; never writes plaintext snapshots by default (`--no-encrypt` exists for staging only) |
| Collection config drift | `schema_version` in every payload; manifest records version; rebuild path = re-run Phase-1 scripts |

**Security:** all data ingested is public security intel (addresses, domains, reports) — no user PII, no private keys, no credentials. Staged data lives outside the repo (`SEED_DATA_DIR`, default `/tmp/rag_seed`). The only secret is `RAG_PERMANENCE_KEY` (secrets manager → env).

---

## 15. Out of Scope / Future

- Real cross-encoder reranker (§8.3 — revisit with hardware headroom).
- Embedding fine-tune on security corpus (would improve retrieval quality; see `embedding_finetune.py` — do not enable until §8.3 gate passes).
- Chain-specific indicator ingestion beyond Ethereum/Solana defaults (BSC/Polygon address blacklists — P2).
- LLM-generated pattern synthesis from hacks (a P2 quality layer: `hacks` → distilled `threat_intel:pattern` docs, reviewed, not auto-committed).
- Historical `news` reindexing (out of scope; news is forward-looking by design).
