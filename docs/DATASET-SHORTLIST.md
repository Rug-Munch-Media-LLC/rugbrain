# Dataset Shortlist — have vs could-have

Measured 2026-10-25 against live Qdrant (`127.0.0.1:6334`, worktree `rb-datasets25`
@ `1e593af`). Point counts are `GET /collections/<name>` → `points_count`, not estimates.
"Exposed" = a purpose-built MCP tool or HTTP route targets the collection. Every
collection is *technically* reachable via `rugbrain_search(collection=…)`, so
"generic-only" means **no tool, prompt or route steers an agent to it** — that is the
gap this document exists to close.

## 1. What we already hold (and do not use)

| Collection | Points | Exposed by | Gap |
|---|---:|---|---|
| `known_scams_8b` | 27,300 | generic-only | no scam tool queries it; `rugbrain_scam_check` goes to `threat_intel_v2` |
| `defi_exploits_8b` | 16,652 | generic-only | richest exploit set we own, unused |
| `security_patterns_8b` | 14,275 | generic-only | registry says 7,897 (2026-10-19) → registry is stale, not the data |
| `live_threats_8b` | 11,164 | alert routes (`alerts/config.py`, `alert_routes.py`) | only collection with a real consumer |
| `crypto_vulnerabilities_8b` | 5,016 | generic-only | — |
| `exploits_8b` | 1,344 | generic-only | stale: newest 2026-05-30 (141 d) |
| `contract_security_8b` | 1,192 | generic-only | — |
| `market_data` | 1,062 | generic-only | DeFiLlama only; no OHLCV/microstructure |
| `contract_audits_8b` | 1,050 | generic-only | 3 audit-firm corpora, no labels |
| `protocol_design_8b` | 58 | generic-only | `protocol_design` base = 1 point (dead) |
| `crime_reports` | 20 | generic-only | OFAC SDN only |
| `solidity_patterns` | 15 | generic-only | effectively empty |
| `solana_patterns_8b` | 5 | generic-only | effectively empty — **biggest content hole** |
| `live_threats` (base) | 0 | — | empty; superseded by `_8b` twin |

**Reading:** ~76 k points of curated security/scam material have no route. Before buying
or ingesting anything, wire `known_scams_8b`, `defi_exploits_8b` and
`security_patterns_8b` into the search tools — that is free capability.

## 2. Ranked shortlist (licence-clean only)

Verified via HF API (`/api/datasets?search=…&full=true`, read 2026-10-25): id, license,
lastModified. Kaggle slugs below are marked [REVIEW] where the licence could not be
confirmed from the dataset card — do not ingest until confirmed.

| # | Dataset | Fetch path | Size / schema | Licence | Updated | Uniquely adds | Action |
|---|---|---|---|---|---|---|---|
| 1 | Slither audited smart contracts | HF `datasets/mwritescode/slither-audited-smart-contracts` | ~1.3 k dl; rows = Solidity source + Slither findings (SWC id, severity) | MIT ✅ | 2022-07-14 | compiler-derived ground truth for `contract_security_8b` (1,192 pts) and `solidity_patterns` (15 pts) | **ingest** → both, findings as text records, source kept as provenance |
| 2 | Smart Contract Vulnerability Dataset | HF `datasets/darkknight25/Smart_Contract_Vulnerability_Dataset` | 159 dl; labelled vulnerable/clean snippets by class | MIT ✅ | 2025-06-21 | labelled positives *and negatives* — we hold almost no clean examples | **ingest** → `solidity_patterns`, `contract_security_8b` |
| 3 | Vulnerability severity classification | HF `datasets/msc-smart-contract-auditing/vulnerability-severity-classification` | 161 dl; text → severity label | MIT ✅ | 2024-05-04 | severity calibration for audit findings (currently ungraded) | **index-only** as eval/label set |
| 4 | Ethereum fraud by activity | HF `datasets/fesevu/ethereum_fraud_dataset_by_activity` | 909 dl; address + activity features + fraud label | CC-BY-4.0 ✅ | 2025-08-27 | labelled fraud accounts to cross-check `wallet_profiles_8b` (40 k, unlabelled) | **ingest** → `wallet_profiles_8b` label slice (attribution required) |
| 5 | Polymarket onchain v1 | HF `datasets/moose-code/polymarket-onchain-v1` | 8.5 k dl; onchain trades/fills | CC-BY-4.0 ✅ | 2026-07-08 | prediction-market flow — we hold **zero** PM data | **ingest** → new `prediction_market_8b` (subset: top markets) |
| 6 | Polymarket order book | HF `datasets/Joseph3222/polymarket-orderbook` | 10.1 k dl; book snapshots | CC-BY-4.0 ✅ | 2026-09-02 | microstructure depth (nothing comparable in `market_data`) | **ingest** → `prediction_market_8b` microstructure slice |
| 7 | Not So Smart Contracts | GitHub `crytic/not-so-smart-contracts` | ~30 curated bug classes with minimal PoC | repo LICENSE = MIT [REVIEW] | active | canonical minimal exploit exemplars for `defi_exploits_8b` retrieval quality | **ingest** after licence read → `solidity_patterns` |
| 8 | Polymarket users | HF `datasets/vgregoire/polymarket-users` | 12.4 k dl; wallet → user/trade rows | CC-BY-4.0 ✅ | 2026-07-06 | wallet-level PM behaviour | **index-only**, PII review first (addresses ⇒ privacy gate) |

Deprioritised but clean: `LolorzoloL/crypto_news` (Apache-2.0, 2023) — **reject**, stale,
duplicates live `news_v2_8b` (275,499 pts).

## 3. Rejections

| Candidate | Path | Reason |
|---|---|---|
| RugPull1K | HF `datasets/fati-80-Sho/RugPull1K` | CC-BY-**NC**-4.0 — non-commercial, incompatible with a commercial product |
| Solana scam signatures | HF `datasets/neuroX3/solana-scam-signatures` | **no licence declared** (card has none) |
| ETH_SmartContractVulnerability_LLM | HF `datasets/biagioboi/ETH_SmartContractVulnerability_LLM` | no licence |
| smart_contract_vulnerability_kaggle | HF `datasets/jhsu12/smart_contract_vulnerability_kaggle` | no licence; mirror of #1/#2 |
| Crypto news headlines | HF `datasets/flowfree/crypto-news-headlines` | no licence + scraped headlines |
| Ethereum fraud detection | Kaggle `vagifa/ethereum-frauddetection-dataset` | licence unverified [REVIEW]; duplicate of #4 |
| SmartBugs curated | GitHub `smartbugs/smartbugs-curated` | licence unverified [REVIEW]; subset of #1/#2 |
| DeFiHackLabs | GitHub `SunWeb3Sec/DeFiHackLabs` | **duplicate** — already `defi_exploits_8b` source `defihacklabs` |
| ScamSniffer DB | GitHub `scamsniffer/scam-database` | **duplicate** — already `known_scams_8b` source `scamsniffer_github` |
| Chainabuse / CryptoScamDB exports | web | ToS forbids redistribution → cannot embed |
| Scraped news archives (any) | — | rights not granted; we already hold `news_v2_8b` + `news_articles_8b` |
| Binance public data | `data.binance.vision` | free to download but **no open licence** (Binance terms) → [REVIEW] before any OHLCV ingestion |

## 4. Order of work

1. Wire the three unused heavyweight collections into tools (free, ~1 day).
2. Ingest #1 + #2 into `solidity_patterns` / `contract_security_8b` (fixes the 15-point hole).
3. Ingest #4 as a labelled wallet slice.
4. Prediction markets (#5/#6) only after 1–3 — new domain, new eval set required.

*Every ingestion must land on the `_8b` twin (4096-d, `dim_policy.COLLECTION_DIM`) and
carry `source`, `license`, `ingested_at` in the payload.*
