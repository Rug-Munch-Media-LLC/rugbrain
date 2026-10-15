# Capability Gaps — Scam Detection & Market Analyst

> Lane R3 audit, 2026-10-15. Every claim below is measured against live Qdrant
> (`http://127.0.0.1:6334`) and the repo at `/srv/work/repos/rugbrain` on that
> date. Where a number is quoted it came from a command in §1; where a claim is
> an inference it is labelled **[inference]**.

---

## 1. AUDIT — what actually exists today

### 1.1 Collections (live point counts, dims)

| Collection | Points | Dim | Fed by | Freshness |
|---|---:|---:|---|---|
| `known_scams_8b` | 26,808 | 4096 | `ingest_multi_chain_scams.py`, `ingest_honeypot.py`, `ingest_tokensniffer.py`, `ingest_scam_watchlist.py`, `scythe_radar_datasets.py` (OFAC) | 🟡 unverified |
| `scam_intel_v2_8b` | 107,878 | 4096 | `blacklists`, `openphish_feed`, `phishdestroy_destroylist` | 🟡 unverified |
| `scam_patterns_8b` | 7,262 | 4096 | `ingest_scam_patterns.py`, `ingest_fraudcoins_holder_concentration.py` | 🟡 unverified |
| `scam_patterns` (base) | 0 | 2560 | — (retired) | ⚪ empty |
| `scam_signatures` | **300** | 4096 | `fraudcoins_signatures.py` only | 🟡 unverified |
| `threat_intel_v2_8b` | 154,434 | 4096 | `scanner`, `defihacklabs`, `telegram` | 2026-10-08 |
| `wallet_v2_8b` | 10,278 | 4096 | `etherscan_malicious`, `etherscan_combined` | 🟡 unverified |
| `wallet_profiles_8b` | 40,355 | 4096 | `etherscan_combined` | 🟡 unverified |
| `market_health_8b` | 627 | 4096 | `market_health.py` (pegs, funding, gas) | 2026-09-11 |
| `stablecoin_market_8b` | 338 | 4096 | `ingest_stablecoin_market.py` (DeFiLlama) | 2026-09-23 |
| `market_data` | 467 | 4096 | `ingest_sources/defillama.py` | 🟡 unverified |
| `liquidation_events` | **0** | 1024 | — declared, never fed | ⚪ empty |
| `exchange_flows` | **0** | 1024 | — declared, never fed | ⚪ empty |
| `rug_events` | **DOES NOT EXIST** | — | `build_rug_events.py` (never run) | ⚪ absent |

Measured with `curl /collections/<name>` and `/points/count` on 2026-10-15.

### 1.2 Tools (MCP surface, `src/rugbrain/rugbrain_mcp/server.py`)

| Tool | Backing route | Backing collections | Verdict |
|---|---|---|---|
| `rugbrain_scam_check` | `GET /api/v1/rag/v2/scam-check` | `known_scams`, `scam_patterns`, `threat_intel_v2`, `rmi_community` | ✅ works |
| `rugbrain_analyze` | same route as `scam_check` | same | ⚠️ **duplicate of `scam_check`** — identical call, different name |
| `rugbrain_scam_radar` | `POST /api/v1/search/multi` | `scam_patterns_8b`, `scam_intel_v2`, `threat_intel_v2` | ✅ works |
| `rugbrain_wallet_profile` | `POST /api/v1/rag/investigate` | `compat_unavailable.investigate` → **no-op stub** | ❌ **returns `{"findings": [], "risk_score": 0.5}`** |
| `rugbrain_market_brief` | `POST /api/v1/search/multi` ×3 | `news_v2`, `social_intel`, `education` | ⚠️ **no market data at all** — it is a news brief |
| `rugbrain_sentiment` | `GET /api/v1/sentiment/token/{addr}` | `social_intel`, `news_articles` | ✅ works |
| `rugbrain_social_sentiment` | `POST /api/v1/search/multi` | `social_intel`, `news_v2` | ✅ works |

### 1.3 The three defects the audit found

**D1 — `scam_signatures` is 300 rows, not 140k.** The Forta labelled-contract
dataset (`forta_malicious_contracts.py`, 139,600 rows, MIT, ships
`contract_creator`) is registered in `ingest_sources/registry.py` but **has
never been ingested**: `points/count` filtered on
`metadata.source == "forta_malicious_contracts"` returns **0**. Only the
FraudCoins holder-concentration source (300 rows) landed. The single largest
public source of *labelled malicious contracts with their creator address* is
sitting unused.

**D2 — `rug_events` does not exist.** `src/rugbrain/rug_events/` is a complete,
tested projection (schema, SolRPDS extractor, scythe CSV parser, deployer
prior-rug aggregation) and `scripts/build_rug_events.py` is its operator
entrypoint — but the collection was never created. `curl /collections/rug_events`
→ `Not found`. The corpus that makes "how many rugs has THIS deployer done"
answerable is written but not built.

**D3 — `score_deployer_risk` fails loudly by design, and nothing replaced it.**
`compat_unavailable.py` raises `DeployerScorerUnavailable` with the message
*"it requires the rug_events corpus (deployer → prior-rug history)"*. That is
correct behaviour — the old stub returned a fabricated `0.5` that the HTTP layer
published as a real wallet verdict. But the corpus it names does not exist (D2),
so the capability is a hard 503 with no path to green.

**D4 — `rugbrain_wallet_profile` is a no-op.** It routes to
`compat_unavailable.investigate`, which returns an empty findings list and a
hardcoded `risk_score: 0.5`. An agent calling it gets a plausible-looking
response that contains no information. This is the same class of defect as the
old `score_deployer_risk` stub.

**D5 — `rugbrain_market_brief` is not a market tool.** It fuses `news_v2`,
`social_intel` and `education`. It never touches `market_health_8b`,
`stablecoin_market_8b`, `market_data`, `liquidation_events` or `exchange_flows`.
A user asking "what is the market doing" gets news headlines.

---

## 2. GAP ANALYSIS — scam detection

Scored against what a best-in-class scam detector needs. **Effort** is
engineering days for a bounded, tested implementation. **Free-first** means the
data source needs no paid key.

| # | Capability | Status | Where it would live | Data source (free-first) | Effort |
|---|---|---|---|---|---|
| S1 | **Deployer → token linkage** | ❌ absent (D2/D3) | `rug_events` + `scam_signatures` join | Forta `contract_creator` (MIT, 139.6k rows, **already registered, never ingested**) | **0.5d** |
| S2 | **Deployer prior-rug history** | ⚠️ code complete, corpus absent | `rug_events.deployer_prior_rugs` | `build_rug_events.py` (SolRPDS + scythe + CRPWarner) | **0.5d** |
| S3 | **Holder concentration** | ✅ present | `scam_signatures` (`top10_pct`) | FraudCoins HF (CC BY 4.0) | done |
| S4 | **LP lock status** | ❌ absent | `scam_signatures` | GoPlus `lp_holders` / RugDoc API (free tier) | 1d |
| S5 | **Honeypot check** | ⚠️ rules only, no live verdict | `known_scams` | Honeypot.is API (free, no key) — `ingest_honeypot.py` has the client, never scheduled | 0.5d |
| S6 | **Cross-chain scam propagation** | ⚠️ fingerprint code exists, no corpus | `cross_chain_8b` (13 pts) | `cross_chain.py` embedder + `rug_events` chain field | 1d |
| S7 | **Wash-trading detection** | ⚠️ `bundle_cluster.py` has the embedder, no live feed | `wallet_clusters` (1024-dim) | DEX trade parquet (chain-indexer) | 2d |
| S8 | **Bytecode similarity to known scams** | ✅ present | `pattern_matching.match_bytecode` | static templates + `scam_patterns_8b` | done |
| S9 | **Sanctioned-address screening** | ✅ present | `known_scams_8b` (OFAC via scythe) | OFAC SDN CSV | done |
| S10 | **Phishing domain/URL intel** | ✅ present | `scam_intel_v2_8b` (107.9k) | OpenPhish, PhishDestroy, URLHaus | done |
| S11 | **Deployer funding source (mixer)** | ⚠️ rule exists, no data | `scam_signatures` | Etherscan txlist (free tier, 5 req/s) | 1d |
| S12 | **Token age / deploy recency** | ⚠️ rule exists, no data | `scam_signatures` | Forta `contract_creation_tx` + block ts | 0.5d |

**The single highest-value gap is S1+S2 together**: they are the same join, the
data is already registered and licensed, and they unblock D3 (the loud 503) and
give `rugbrain_analyze` a real deployer verdict instead of a bytecode-only one.

---

## 3. GAP ANALYSIS — market analyst

| # | Capability | Status | Where it would live | Data source (free-first) | Effort |
|---|---|---|---|---|---|
| M1 | **Stablecoin peg deviation** | ✅ present | `market_health_8b` | CoinGecko simple/price | done |
| M2 | **Perp funding rates** | ✅ present | `market_health_8b` | Binance `fapi/v1/fundingRate` | done |
| M3 | **Gas price** | ✅ present | `market_health_8b` | Etherscan gasoracle | done |
| M4 | **Fear & Greed index** | ✅ present | `fear_index` + `market_context` | Alternative.me | done |
| M5 | **TVL / chain activity** | ✅ present | `market_data`, `market_context` | DeFiLlama `/v2/chains` | done |
| M6 | **Market regime signal** | ❌ absent | new `market_regime` module | derived from M1+M2+M4 (no new source) | **0.5d** |
| M7 | **Whale flows** | ⚠️ `smart_money_tracker.py` exists, 4 hardcoded wallets, no collection | `exchange_flows` (0 pts) | Etherscan txlist | 1d |
| M8 | **Exchange net flows** | ⚠️ `exchange_flow.py` exists, never scheduled | `exchange_flows` (0 pts) | Etherscan balance + txlist | 1d |
| M9 | **Liquidation heatmap** | ⚠️ `liquidation_heatmap/` exists, `liquidation_events` 0 pts | `liquidation_events` | Binance/Bybit public WS | 2d |
| M10 | **Correlation matrix** | ❌ absent | new module | CoinPaprika tickers (already fetched) | 1d |
| M11 | **Funding-rate regime (crowding)** | ❌ absent | `market_regime` | Binance funding (already fetched) | 0.5d |
| M12 | **Price/TVL freshness SLA** | ⚠️ `market_health_8b` newest 2026-09-11 (34d stale) | ops | `check-collection-staleness.py` | 0.5d |

**The single highest-value market gap is M6+M11**: a market regime signal
derived entirely from data already fetched and stored (pegs, funding, F&G). No
new upstream, no new key, and it turns three disconnected numbers into one
actionable label that the scanner can consume.

---

## 4. IMPLEMENTED — S1: deployer → token linkage

**Chosen because** it is the highest-value gap that is bounded, testable, and
needs no new upstream: the Forta dataset is already registered, already
licensed (MIT), and already ships `contract_creator`.

**What was built** — `src/rugbrain/deployer_linkage.py`:

* `link_deployer_tokens(rows)` — pure function. Takes Forta-shaped rows
  (`contract_address`, `contract_creator`, `malicious`, `contract_tag`) and
  returns `{deployer: {tokens: [...], malicious_count, benign_count,
  total_count, risk}}`.
* `deployer_risk(entry)` — maps a linkage entry to a verdict
  (`critical` / `high` / `medium` / `low` / `unknown`) with the rule stated in
  the docstring, so the number is auditable rather than a magic constant.
* `linkage_to_docs(linkage)` — emits one `IngestDoc` per deployer into
  `scam_signatures`, so the linkage is queryable by deployer address.

**Why this is the right shape**: it is a *pure* projection over rows the
existing `FortaMaliciousContractsSource` already produces. It does not fetch,
does not embed, and does not write — so it is unit-testable against real rows
and cannot silently degrade when a network call fails.

**Tests** — `tests/test_deployer_linkage.py`, against real Forta row shapes.

**Not done (deliberately)**: the ingest run itself. Running
`FortaMaliciousContractsSource` pulls a 1.4 GB parquet and embeds 139.6k rows;
that is an operator action with a cost, not a lane action. The linkage module is
the part that was missing and is now correct; the run is one command
(`python -m rugbrain.ingest_sources.cli --source forta_malicious_contracts`).

---

## 5. Open questions

1. **Is `scam_signatures` the right home for deployer linkage?** It is declared
   at 4096 with no twin and 300 rows. A deployer-keyed corpus is a different
   entity type from a token-keyed one. **[REVIEW]** — a `deployer_profiles`
   collection may be cleaner, but that is a new owned name and a new lane
   declaration.
2. **Should `rug_events` be built?** The code is complete and tested. Building
   it needs the embed lane env sourced and a Qdrant write. It is the unblock for
   D3. **[REVIEW]** — operator decision on when to spend the embed budget.
3. **`rugbrain_analyze` vs `rugbrain_scam_check`** are the same call. One should
   be removed or repointed at `/analyze/{address}/full` (which does more).
4. **`rugbrain_wallet_profile` returns a no-op.** It should either be wired to a
   real implementation or removed — a tool that returns `risk_score: 0.5` for
   every address is worse than a 503.
5. **`market_health_8b` is 34 days stale** (newest 2026-09-11). The monitor
   exists; the loop is not running. Same class as D2.
