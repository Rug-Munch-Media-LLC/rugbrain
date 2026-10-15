# RugBrain Agent Guide

> **Audience:** every agent on the server (opencode, subagents, chat bridge).
> **Status:** verified against the live tool surface + Qdrant, 2026-10-15.
> **Source of truth for the tool surface:** `src/rugbrain/rugbrain_mcp/server.py`
> (FastMCP facade over the RugBrain HTTP API at `127.0.0.1:8012`).

RugBrain is the fleet's **knowledge** side: news, scams, exploits, incidents,
wallet/entity intel, regulation, market mood. It is *not* a code search engine.
If you are looking for a function, a contract, a library, or a security
*pattern-as-code*, that is **ContextCrypto** — see the boundary table below.

---

## 1. The boundary: knowledge → RugBrain, code → ContextCrypto

| You want… | Use | Why |
|---|---|---|
| "Is this address/deployer known? What did the news say? Recent exploits on Base?" | **RugBrain** (`rugbrain_*`) | Collected knowledge: news, scam reports, incident write-ups, wallet intel |
| "What IS this symbol / who calls it / how do WE build X / is this pattern malicious?" | **ContextCrypto** (`search_reference`, `rmi_playbook`, `query_security_context`, `query_exploits`) | Code, symbols, library source, curated verdicts, playbooks |
| Chain state (balances, tx, live price) | **rugmunch-data / chain tools** | Neither RAG system owns live chain state |

The two systems share **one Qdrant instance** (`rmi-qdrant-v2`, host `:6334`)
and **one embedder** (`Qwen/Qwen3-Embedding-8B`, 4096-dim on the `_8b` lane).
The split is **collection + consumer surface**, not infrastructure. Some data
(exploit/hack/scam) deliberately appears in both — RugBrain holds the *write-up*,
ContextCrypto holds the *PoC code*. Do not send the same question to both.

> Full design: `docs/RAG-MCP-BOUNDARY.md`. One-line rule: `docs/DATA-PLACEMENT.md`.

---

## 2. Tool-by-tool: when to use it, and a concrete call

All tools are exposed by the `rugbrain-mcp` server. Args below are the real
signatures from `server.py`.

### `rugbrain_search` — one collection, semantic
Use when you know **which** collection holds the answer. Default collection is
`threat_intel_v2`.

```
rugbrain_search(query="reentrancy audit finding", collection="contract_audits", limit=5)
rugbrain_search(query="SEC enforcement action crypto", collection="regulation_enforcement")
```

### `rugbrain_search_multi` — several collections, RRF-fused
Use when the answer could live in more than one collection and you want one
ranked list. Fuses with Reciprocal Rank Fusion, then reranks.

```
rugbrain_search_multi(query="USDC depeg", collections=["news_v2","social_intel","stablecoin_market"], limit=10)
```

### `rugbrain_scam_check` — address verdict (3-pillar)
Cheapest first look at an unknown token/contract/wallet address. Returns the
scam/wallet/contract verdict.

```
rugbrain_scam_check(address="0x...", chain="ethereum", limit=10, tier="free")
```

### `rugbrain_analyze` — full risk analysis for an address
Same endpoint as `scam_check` but the "full" 3-pillar analysis; use when you
need the complete picture rather than a quick verdict.

```
rugbrain_analyze(address="0x...", chain="ethereum")
```

### `rugbrain_wallet_profile` — entity investigation
Heavier than `scam_check`: multi-hop fund-flow graph, related wallets. Use when
you need the **entity behind** an address, not just a risk verdict.

```
rugbrain_wallet_profile(address="0x...", max_hops=2)
```

### `rugbrain_news` — unified feed
YouTube + podcasts + newsletters + news in one feed. Empty `query` = latest.

```
rugbrain_news(limit=20)
rugbrain_news(query="stablecoin", sources="news,newsletter", min_date="2026-10-01")
```

### `rugbrain_market_brief` — "what's happening right now"
Fuses `news_v2` + `social_intel` + `education`, reranked, each item tagged with
its source collection and score. Use for a general market read.

```
rugbrain_market_brief(limit=10)
rugbrain_market_brief(query="Bitcoin", limit=5)
```

### `rugbrain_social_sentiment` — social/narrative mood
Social intel first (crypto tweets with sentiment labels), news second. Distinct
from `rugbrain_sentiment`, which aggregates sentiment for a **token address**.

```
rugbrain_social_sentiment(query="SOL", limit=10)
```

### `rugbrain_sentiment` — token-address sentiment
Aggregate sentiment for a **token/contract address** from on-chain/social/news.

```
rugbrain_sentiment(address="0x...", chain="ethereum", include_social=True, include_news=True)
```

### `rugbrain_scam_radar` — scam/shill read for a topic
Searches `scam_patterns_8b` + `scam_intel_v2` + `threat_intel_v2`, flags shill
categories, extracts contract addresses from the text.

```
rugbrain_scam_radar(query="pump and dump", limit=10)
```

### `rugbrain_alerts` — recent alerts
Alerts detected in the last N minutes (1–1440).

```
rugbrain_alerts(minutes=60)
```

### `rugbrain_vector_landscape` — collection map
Live map of every vector space: collections, dims, point counts, mismatch flags.
Use to check whether a collection actually holds data before querying it.

```
rugbrain_vector_landscape()
```

### `rugbrain_health` — service health
`full=False` → `/health`; `full=True` → `/api/v1/health/full` (subsystem detail).

```
rugbrain_health()
rugbrain_health(full=True)
```

---

## 3. Collections: what each holds

Point counts are from the live `rugbrain_vector_landscape()` call (2026-10-15).
**Query the bare name** (e.g. `contract_audits`); the API resolves it to the
`_8b` lane twin internally (see §4).

| Collection (query this) | Points | Holds |
|---|---:|---|
| `threat_intel_v2` | 154,275 | Scam/threat intel, scanner findings, incident reports (default) |
| `news_v2` | 270,237 | Telegram/news firehose, trending tokens |
| `scam_intel_v2` | 107,878 | Phishing/blacklist feeds (openphish, phishdestroy) |
| `scan_intel` | 60,483 | Scanner output |
| `wallet_profiles` | 40,355 | Wallet/entity profiles (etherscan) |
| `known_scams` | 26,808 | Known scam addresses (scamsniffer, metamask phishing) |
| `defi_exploits` | 16,651 | DeFi exploit write-ups (defihacklabs, rekt) |
| `live_threats` | 11,164 | Live threat signals (clickhouse dex/oracle/new-address) |
| `wallet_v2` | 10,278 | Wallet intel v2 |
| `scam_patterns` | 7,262 | Scam pattern signatures (cryptoscamdb) |
| `security_patterns` | 7,063 | Security patterns (advisories, known-bad) |
| `education` | 5,293 | Educational content (solana_compass, bankless) |
| `crypto_vulnerabilities` | 4,918 | CVEs/advisories (ossf, cisa_kev, swc) |
| `regulation_enforcement` | 5,084 | SEC/CFTC actions, sanctions (opensanctions, seed_landmark) |
| `news_articles` | 2,398 | News articles (coindesk, cointelegraph) |
| `podcast_episodes` | 1,462 | Podcast episodes |
| `exploits` | 1,344 | Exploit records |
| `contract_security` | 1,180 | Contract security (openzeppelin, safe) |
| `contract_audits` | 1,050 | Audit findings (trailofbits, openzeppelin, slowmist) |
| `security_feeds` | 874 | Security blogs/advisories RSS |
| `coding_knowledge` | 833 | Coding knowledge |
| `market_health` | 627 | Market health snapshots |
| `market_data` | 467 | Market data (defillama) |
| `stablecoin_market` | 338 | Stablecoin market (defillama) |
| `scam_signatures` | 300 | Scam signatures |
| `github_audits` | 199 | GitHub audit reports |
| `research_v2` | 158 | Research blogs (trailofbits, neodymium, zellic) |
| `security_alerts` | 125 | Security alerts |
| `protocol_design` | 58 | Protocol design patterns |
| `crime_reports` | 20 | Crime reports (ofac_sdn) |
| `newsletters` | 17 | Newsletters |
| `social_intel` | 1 | Social intel (crypto tweets) — **thin, being repopulated** |
| `hacks_v2` | 13 | Hack records |
| `cross_chain` | 13 | Cross-chain patterns |
| `solana_patterns` | 5 | Solana patterns |
| `privacy_protocol` | 4 | Privacy protocols (tornado_cash) |
| `zk_circuits` | 130 | ZK circuits |

> The full, auto-generated list (91 collections) is `docs/COLLECTION_REGISTRY.md`.
> Collections with **0 points** are stubs/retired lanes — do not query them.

---

## 4. The `_8b` suffix — read this before naming a collection

RugBrain migrated to a 4096-dim lane (`Qwen3-Embedding-8B`). The `_8b` suffix is
a **lane marker**, not a separate public collection. Two rules:

1. **Query the bare name** (`contract_audits`, `regulation_enforcement`,
   `news_v2`, …). The API's lane resolver (`qdrant_store/lane_resolver.py`,
   `resolve_collection`) maps the bare name to its `_8b` twin at read/write time.
2. **Do not query the `_8b` name directly.** It is not in the public allowlist
   (`knowledge_boundary_tables.PUBLIC_COLLECTIONS`) for most collections, so it
   returns `403 collection_not_available`. (A few `_8b` names are allowlisted
   for the canary lane and will resolve, but the bare name is always correct.)

### Resolution of the "empty collections" report

`contract_audits` and `regulation_enforcement` were reported empty from the
RugBrain side. **Verified against Qdrant (`127.0.0.1:6334`) on 2026-10-15:**

| Name | Qdrant points | Public? | Verdict |
|---|---:|---|---|
| `contract_audits` | *(no such collection)* | ✅ | bare name — resolves to `_8b` twin |
| `contract_audits_8b` | 1,050 | ❌ | **403** if queried directly |
| `regulation_enforcement` | 0 (2560-dim stub) | ✅ | bare name — resolves to `_8b` twin |
| `regulation_enforcement_8b` | 5,084 | ✅ | holds the data |

**Both collections serve data via the bare name.** Live proof:

```
rugbrain_search(query="reentrancy audit finding", collection="contract_audits")   → 3 hits
rugbrain_search(query="SEC enforcement action crypto", collection="regulation_enforcement") → 3 hits
```

The "empty" report was **stale** — it read the bare `regulation_enforcement`
stub (0 pts) and the non-existent `contract_audits` name instead of the
resolved `_8b` twins. **No code fix is needed**; the resolver is working. The
only real inconsistency is that `contract_audits_8b` is not in
`PUBLIC_COLLECTIONS` while `regulation_enforcement_8b` is — harmless, because
callers should use the bare name. `[REVIEW]` if you want the allowlist made
uniform.

---

## 5. Known issues (verified 2026-10-15)

- **`rugbrain_market_brief`, `rugbrain_social_sentiment`, `rugbrain_scam_radar`
  returned `401 Unauthorized`.** Root cause: the async market-specialist surface
  (`_call_search_multi`) did not send the `X-API-Key` header that the sync
  `_call()` path sends, and `RAGAuthMiddleware` requires it on every non-health
  path. **Fixed** in `server.py` by adding `X-API-Key` to `HEADERS`. Verified:
  without the key the endpoint returns 401; with it, results return.
- **`social_intel` is thin (1 point).** A repopulation is in flight; treat
  social reads as low-coverage until it lands.
- **`rugbrain_alerts` returned 0 alerts** over 1440 min at verification time —
  the endpoint is healthy, there were simply no alerts in the window.

---

## 6. Quick decision tree

```
Is it about CODE (symbol, contract, library, pattern-as-code)?
  → ContextCrypto (search_reference / rmi_playbook / query_security_context)

Is it about a real EVENT, ADDRESS, or REPORT (news, scam, exploit, wallet)?
  → RugBrain
      known address?            → rugbrain_scam_check / rugbrain_analyze
      entity behind an address? → rugbrain_wallet_profile
      a topic/ticker mood?      → rugbrain_social_sentiment
      what's happening now?     → rugbrain_market_brief / rugbrain_news
      a specific collection?    → rugbrain_search
      several collections?      → rugbrain_search_multi
      is a collection live?     → rugbrain_vector_landscape
      is the service up?        → rugbrain_health
```
