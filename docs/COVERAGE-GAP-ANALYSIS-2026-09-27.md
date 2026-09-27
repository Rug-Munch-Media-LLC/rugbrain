# RugBrain Coverage Gap Analysis — 2026-09-27

Read-only audit of `rmi-qdrant-v2` (`127.0.0.1:6334`). No writes, no reindex, no restarts.
84 collections exist; 41 crypto-relevant were probed. **Nothing was mutated.**

## 1. The real inventory

Vector dim is the discriminator: `2560` = CorpusCrypto, `4096` = secondary embedder,
`1024` = provisioned-empty. **No collection has a payload index** (`payload_schema` is
`{}` everywhere) — every query is unfiltered vector search.

| Collection | Points | Vec | Indexed? | Newest doc |
|---|---:|---:|:--:|---|
| rag_documents | 502,674 | 2560 | no | n/a |
| news_v2 | 150,000 | 2560 | no | 2026-09-27 |
| scam_intel_v2 | 106,871 | 2560 | no | no date field |
| wallet_profiles | 40,355 | 2560 | no | no date field |
| **scan_intel** | 36,657 | 2560 | no | 2026-09-27 |
| threat_intel_v2 | 36,422 | 2560 | no | 2026-09-27 |
| known_scams | 26,720 | 2560 | no | no date field |
| social_intel | 20,984 | 2560 | no | 2026-09-26 |
| defi_exploits | 16,648 | 2560 | no | 2026-09-22 |
| live_threats | 11,164 | 2560 | no | 2026-09-27 |
| wallet_v2 | 9,657 | 2560 | no | no date field |
| scam_patterns | 7,102 | 2560 | no | no date field |
| regulation_enforcement | 5,081 | 2560 | no | **2023-11-21** |
| crypto_vulnerabilities | 4,915 | 2560 | no | no date field |
| master_skills | 4,495 | 2560 | no | n/a |
| education | 4,336 | 2560 | no | 2026-09-25 |
| news_articles | 2,354 | 2560 | no | 2026-09-27 |
| security_patterns | 2,005 | 2560 | no | 2026-09-24 |
| **exploits** | 1,344 | 2560 | no | 2026-09-22 |
| contract_security | 1,099 | 2560 | no | no date field |
| contract_audits | 902 | 2560 | no | no date field |
| security_feeds | 874 | 2560 | no | 2026-09-07 |
| market_health | 627 | 2560 | no | 2026-09-11 |
| stablecoin_market | 336 | 2560 | no | 2026-09-27 |
| github_audits | 199 | 2560 | no | 2026-09-27 |
| research_v2 | 158 | 2560 | no | 2026-09-25 |
| security_alerts | 125 | 2560 | no | 2026-09-27 |
| protocol_design | 58 | 2560 | no | 2026-09-15 |
| podcast_episodes | 26 | 4096 | no | 2026-09-24 |
| newsletters | 17 | 4096 | no | 2026-09-24 |
| cross_chain | 13 | 2560 | no | no date field |
| hacks_v2 | 8 | 2560 | no | 2026-08-30 |
| public_crypto_patterns | 2 | 2560 | no | no date field |
| liquidation_events | **0** | 1024 | no | empty |
| token_unlocks | **0** | 1024 | no | empty |
| exchange_flows | **0** | 1024 | no | empty |
| hyperliquid | **0** | 1024 | no | empty |
| solana_patterns | **0** | 2560 | no | empty |
| scam_patterns (listed) / scams_v2 | **0** | — | — | empty |
| wallet_clusters | **0** | — | no | empty |
| scanned_tokens | **0** | — | no | empty |
| youtube_videos | **0** | — | no | empty |
| scanner_memory | **0** | — | no | empty |

`indexed_vectors_count` reads `0` for 18 collections (incl. `exploits`, `contract_audits`,
`security_feeds`, `market_health`) — a **telemetry bug, not a real outage**: config declares
a valid vector and direct scroll returns full content. Do not act on that field.

## 2. Advertised vs actual

`knowledge_boundary.PUBLIC_COLLECTIONS` holds **28** names; the MCP tool description
advertises **12**. So the real fault is *not* only documentation — the allowlist and the
tool text disagree with each other.

**Gated INTERNAL (by design, `INTERNAL_COLLECTIONS`)** — 7: `agent_vectors`,
`codebase_reference`, `codebase_reference_v2`, `master_skills`, `protocol_design`,
`security_patterns`, `security_patterns_test`.

**Public but ABSENT from Qdrant** — 3, permanently 403: `audit_findings`,
`rmi_community`, `solidity_patterns`.

**Unadvertised yet reachable and valuable** — 16, invisible to an agent that reads only
the tool description: `defi_exploits` (16,648 — the richest exploit ground, see §5),
`live_threats`, `stablecoin_market`, `market_health`, `security_feeds`, `security_alerts`,
`social_intel`, `contract_security`, `hacks_v2`, `regulation_enforcement`, `education`,
`research_v2`, `crypto_vulnerabilities`, `news_articles`, `wallet_v2`, `contract_audits`.

**Not public and not internal — orphaned** (403, no owning class):
`exploits` (1,344) and `scan_intel` (36,657). `scan_intel` holds **today's** scan telemetry
and is the single most fleet-relevant collection in the corpus, and it is unreachable.

## 3. Per fleet-job coverage

| Fleet job | Servable? | From where / what is missing |
|---|---|---|
| Honeypot / sell-tax detection | **No** | Needs per-token buy/sell tax, sell-sim result, LP-lock state. `scam_patterns` (7,102) is *domain* blacklists only; `scan_intel` has `SENTINEL_LP_LOCK` flags but **no prose body**, and is 403. `contract_security` is generic OpenZeppelin code. |
| Risk scoring | **Partial** | `scan_intel` (36,657, newest 2026-09-27) holds `risk_score`/`risk_level`/`risk_flags` for solana/bsc/base — 403, unqueryable, no text. |
| Wallet labeler | **Yes** | `wallet_profiles` (40,355, etherscan_combined) + `wallet_v2` (9,657) + `known_scams` (26,720, 46% carry addresses). Strongest area. |
| ClickHouse transfer ingestion | **Yes** | `live_threats` (11,164, `clickhouse-dex`, tx_hash + sender/recipient + amount_usd, newest 2026-09-27). |
| Telegram admin alerts | **Yes** | `security_alerts` (125) + `live_threats` both public, both dated 2026-09-27. |
| Rug-pull / scam pattern learning | **Weak** | `scam_patterns` is CryptoScamDB **domain** entries, not on-chain rug mechanics. No `token_unlocks`, no `liquidation_events`. |
| Regulatory context | **Stale** | `regulation_enforcement` newest doc = **2023-11-21**, ~3 years behind. 5,081 points of dead history. |
| Market/TVL context | **Partial** | `stablecoin_market` fresh (2026-09-27) but 336 pts; `market_health` 627 pts, newest 2026-09-11. |
| Bridge / exploit reasoning | **Yes, best-in-class** | `defi_exploits` (16,648) — Ronin returns attacker address, both tx hashes, $625M loss, attack block, `forge` repro command. |
| Cross-chain / Solana-specific | **No** | `cross_chain` = 13, `solana_patterns` = 0, `hyperliquid` = 0. |

Address density (share of 300-doc samples containing a chain address) — this is the
labeler fuel, and it is concentrated: `wallet_v2` 100%, `wallet_profiles` 100%,
`live_threats` 100%, `defi_exploits` 50%, `known_scams` 46%, `social_intel` 25%,
`threat_intel_v2` 16%, `scam_intel_v2` 16%, `news_v2` 7%, `scan_intel` **0%**.

## 4. Ranked shortlist

1. **Un-gate `scan_intel` and give it a text body.** Source: none needed — it already
   exists, already fresh (2026-09-27), already holds `risk_score`/`risk_flags` for
   solana/bsc/base. Populate: itself; re-emit each point as
   `"<symbol> <chain> risk=<level> score=<n> flags=<...> lp_locked=<bool>"`.
   Beats everything else: it is the fleet's own scan telemetry, the only corpus evidence
   of real scanner behaviour, and it is 100% unreachable. Cheapest possible win.
2. **Add `token_ownership` (deployer/mint-authority/top-holder concentration).** Source:
   existing ClickHouse `token_transfer` + the GoPlus/TokenSniffer lane already wired in
   the Databus. Populate: new collection keyed by token address.
   Why: this is precisely what a scam scanner must know and the single largest blind
   spot — today a token can be scanned with no notion of who minted it or who holds it.
   Note an address→label index is *not* needed: `wallet_profiles` + `wallet_v2` already
   cover lookup, so a keyword payload index there is the cheaper fix.
3. **Add `lp_and_liquidity` (LP lock state, LP holder identity, lock expiry, burn).**
   Source: GoPlus `lp_holders`/DEXScreener pair data, already proxied in the Databus.
   Populate: new collection keyed by pair/token.
   Why: `SENTINEL_LP_LOCK` is the single most common flag in `scan_intel` (987/1000
   sampled) and there is *zero* ground truth for it anywhere in the corpus.
4. **Refresh `regulation_enforcement`.** Source: SEC/DOJ/CFTC/FinCEN/MAS press feeds.
   Populate: same collection, additive. Why: newest doc is 2023-11-21. A
   "compliance" surface that is three years stale is worse than an absent one, because
   it looks populated.
5. **Wire the 3 phantom allowlist names** (`audit_findings`, `rmi_community`,
   `solidity_patterns`) to real collections or drop them. Zero data cost, removes a
   permanent 403 from the public API.

Deliberately **not** on the list: a standalone bridge-exploit collection (`defi_exploits`
already wins), liquidation/token-unlock feeds (zero-day, but the fleet's alert path is
transfer-driven, not unlock-driven — re-rank if the product adds unlock alerts), and any
new `_8b` pair (owned by another agent).

## 5. Duplicates — reported, not fixed

- **`hacks_v2` (8 pts) is dead.** All 8 are mega-cap headline losses (Ronin $624M,
  Binance Bridge $570M, FTX $450M, Bybit $1.4B). The three names it *appears* to
  duplicate (`exploits`, `defi_exploits`, `live_threats`) are all far richer. It is
  neither canonical nor a useful subset — it is a stale headline list. **Retire**
  (leaving it in `PUBLIC_COLLECTIONS` keeps exposing it as authoritative).
- **`exploits` vs `defi_exploits` are NOT duplicates.** Content-hash harvest of the
  full 1,344 + 16,648 gave **0 overlap** (`exploits ∩ defi_exploits = 0`,
  `exploits ∩ hacks_v2 = 0`, `defi ∩ hacks_v2 = 0`). Different granularity: `exploits`
  = atomic vulnerability class + code block + chain; `defi_exploits` = per-incident
  post-mortem with attacker addresses, tx hashes, loss USD, attack block, repro command.
  **Canonical for incidents: `defi_exploits`** (16,648 vs 1,344, strictly richer,
  deduped, dated 2026-09-22). `exploits` is only worth keeping for its atomic
  class+code dimension — but it is 403, so today it serves nobody.
- **`_8b` siblings** — 30 of 84. Owned by another agent, untouched, out of scope here.
