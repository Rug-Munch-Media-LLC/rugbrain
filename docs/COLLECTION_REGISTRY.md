# Qdrant Collection Registry

Source of truth for the RugBrain Qdrant collections: what each collection
holds, which source feeds it, and how fresh that source is. The RAG
freshness agent and operators use this to decide whether a collection is
safe to rely on for time-sensitive risk signals.

Inventory captured 2026-08-08 against Qdrant at localhost:6333.

## Active / fresh collections

| Collection | Points | Source(s) | Freshness | Status |
|------------|-------:|-----------|-----------|--------|
| `threat_intel` (→ `threat_intel_v2`) | 11,918 | DeFiHackLabs PoCs, **DeFiLlama hacks API** | Live — cron every 6h | ✅ live |
| `defi_exploits` (→ alias) | 13,378 | DeFi Security Incident DB, DeFiHackLabs, Web3Hacks | Live (git repos, checkpointed) | ✅ live |
| `news` (→ `news_v2`) | 4,124 | rmi-backend RSS ingest (15-min) → news_to_rag | Live — 15-min cron | ✅ live |
| `crime_reports` | 1,387 | Chainabuse + community reports | Live (checkpointed) | ✅ live |
| `wallet` (→ `wallet_v2`) | 13,499 | wallet labels / entity resolution | Static + refresh | ✅ live |
| `known_scams` | 9,891 | CryptoScamDB blacklist | 🔒 frozen 2022-06-28 | ⚠️ historical-only |
| `scam_patterns` (→ `rug_timeline`) | 9,921 | CryptoScamDB blacklist | 🔒 frozen 2022-06-28 | ⚠️ historical-only |
| `scam_intel` (→ `scam_intel_v2`) | 600 | curated scam intelligence | Mixed | ✅ live |
| `regulation_enforcement` | 16 | SEC litigation/press RSS + landmark seed | Live — cron every 6h | ✅ live (NEW) |
| `security_feeds` | 66 | aggregated security feeds | Live | ✅ live |
| `security_alerts` | 83 | live threat alerts | Live | ✅ live |
| `crypto_vulnerabilities` (→ `vuln_patterns`) | 986 | SlowMist KB, SWC Registry | Git repos, checkpointed | ✅ live |
| `contract_audits` | 502 | OpenZeppelin + audit reports | Git repos, checkpointed | ✅ live |
| `hacks` (→ `hacks_v2`) | 312 | Rekt News leaderboard (historical) | 🔴 source dead 2026 | ⚠️ historical-only |
| `solidity_patterns` | 1,059 | solidity knowledge base | Static seed | ✅ static |
| `solana_patterns` | 259 | solana/rust knowledge base | Static seed | ✅ static |
| `coding_knowledge` | 1,826 | codebase knowledge | Static seed + refresh | ✅ static |
| `education` | 1,372 | education corpus | Static seed | ✅ static |
| `research` (→ `research_v2`) | 60 | research documents | On-demand | ✅ on-demand |
| `rmi_agent_memory` | 15 | agent memories | On-demand | ✅ on-demand |
| `rmi_community` | 26 | community reports | On-demand | ✅ on-demand |

Remaining (small / low-volume / tooling): `audit_findings` (10),
`contract_security` (221), `cryptography` (30), `github_audits` (137),
`live_threats` (1), `news_articles` (352), `rgm_*` (wallet graph 10,000),
`scams_v2` (300), `scanner` (→ `scanner_v2`, 191), `scanner_feedback` (0),
`social_intel` (1), `standards` (15), `transaction_patterns` (0),
`wallet_embeddings` (0), `whale_transfers` (0).

## ⚠️ Historical-only flags

### `scam_patterns` + `known_scams` — CryptoScamDB frozen 2022-06-28

Both collections are built from the CryptoScamDB blacklist
(github.com/CryptoScamDB/blacklist), whose last commit is **2022-06-28 —
over 4 years stale** as of 2026-08-08. The address/URL blacklist data
does not expire (a phishing wallet stays phishing), so it remains useful
as a *historical* blocklist, but it MUST NOT be treated as evidence of
current scam activity.

- **Replacement source:** DeFiLlama hacks API (`api.llama.fi/hacks`) now
  feeds fresh exploit incidents into `threat_intel` via
  `scripts/ingest_defillama_hacks.py` (612 incidents, 32 in last 30 days).
  `defi_exploits` covers the same incident universe with root-cause
  analysis from DeFiHackLabs/DeFi-Security-Incident.
- **Action for consumers:** treat `scam_patterns`/`known_scams` hits as
  historical context only; rely on `threat_intel` + `defi_exploits` +
  `crime_reports` for current activity.

### `hacks` (→ `hacks_v2`) — Rekt News source dead

Rekt News RSS (`https://rekt.news/feed/`) returns HTTP 500 and the
leaderboard page no longer embeds the `__NEXT_DATA__` JSON blob that
`ingest_rekt_news.py` / `ingest_scam_knowledge.py`'s `rekt_news` source
depend on (site now renders client-side). The existing 312 points are
historical leaderboard entries. Replacement: DeFiLlama hacks API (same
incident universe, structured, maintained) → `threat_intel`.

## New sources (2026-08-08)

### `defillama_hacks` → `threat_intel`

`scripts/ingest_defillama_hacks.py` pulls `https://api.llama.fi/hacks`
(612 incidents, structured: date, amount, chain, technique,
classification, returned funds). Idempotent UUIDv5 point IDs
(`defillama_hacks:{name}:{date}`). Cron incremental mode: `--days 90`.

### `regulation_enforcement` (new collection)

`scripts/ingest_regulation_enforcement.py` creates the hybrid
(dense + BM25 sparse) collection and ingests:

1. SEC litigation releases RSS (crypto-filtered)
2. SEC press releases RSS (crypto-filtered)
3. 16 landmark SEC/DOJ/CFTC enforcement cases (2020-2024 seed)

SEC crypto enforcement is rare but high-signal for risk analysis — the
rolling feeds keep it fresh, the seed gives immediate precedent coverage.
Payload carries `agency`, `action_type`, `case_url`, `amount_usd`, `date_ts`
for filtering.

## Cron wiring

| Job | Schedule | Script |
|-----|----------|--------|
| News RSS → Postgres → `news_v2` | every 15 min | `scripts/rmi-news-ingest.sh` |
| News + scam knowledge + continuous ingest | hourly | `/root/scripts/rag-knowledge-refresh.sh` |
| **Threat intel + regulation** | **every 6h** | **`scripts/rmi-threat-intel-ingest.sh`** |
| RAG backup | daily 03:00 | `fleet-infra/scripts/backup-rag.sh` |
