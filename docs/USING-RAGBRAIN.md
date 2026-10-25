# Using RugBrain — agent guide

**Audience:** any agent (opencode subagent, chat bridge, script) that needs grounded
crypto knowledge. **Verified against:** `src/rugbrain/rugbrain_mcp/tools_search.py`,
`tools_intel.py`, `tools_market.py`, `src/rugbrain/routes/ta.py` — 2026-10-25.
**Read first:** [`docs/INDEX.md`](INDEX.md). **Boundary rule:** knowledge → RugBrain,
code/symbols → ContextCrypto (`docs/RAG-MCP-BOUNDARY.md`).

RugBrain is a **retrieval** service, not an authority. Everything it returns is a ranked
chunk with a collection and a score. Your job is to keep those two labels attached to
every sentence you write from it.

---

## 0. Preconditions — check the lane before you query

| Check | Call | Meaning if it fails |
|---|---|---|
| Server up | `rugbrain_health()` | Nothing else will work; report the outage, do not improvise. |
| Deep check | `rugbrain_health(full=True)` | Qdrant/embedder/rerank detail. |
| What is indexed | `rugbrain_vector_landscape()` | Point counts per collection — your coverage map. |

A fresh collection answers with an **empty result set, not an error**. Empty ≠ broken,
and empty ≠ "the answer is no".

---

## 1. Searching the corpus

### `rugbrain_search` — one collection
```
rugbrain_search(query="reentrancy audit finding", collection="contract_audits", limit=5)
rugbrain_search(query="SEC enforcement action crypto", collection="regulation_enforcement", rerank=True)
```
Signature: `(query, collection="threat_intel_v2", limit=10, rerank=True)`.
`rerank=True` is the default; it costs a Qwen3-Reranker pass and is worth it for named
entities and addresses.

**Verified collection names** (from the tool docstrings — do not invent others):
`threat_intel_v2`, `defi_exploits`, `scam_intel_v2`, `known_scams`, `scam_patterns`,
`news_v2`, `wallet_v2`, `contract_audits`, `crypto_vulnerabilities`,
`regulation_enforcement`, `education`, plus the market-lane names in §4
(`social_intel`, `scam_patterns_8b`).

### `rugbrain_search_multi` — several collections, RRF-fused
```
rugbrain_search_multi(
    query="USDC depeg",
    collections=["news_v2", "social_intel"],
    limit=10,
    limit_per_collection=20,
    rerank=True,
)
```
Signature: `(query, collections=None, limit=10, limit_per_collection=20, rerank=True)`.
Use this when you do not know which collection holds the answer — it fuses with
Reciprocal Rank Fusion before reranking. **Prefer it over guessing a collection.**

### Worked example — one question, one cited answer
Question: *"What was reported about the USDC depeg?"*

```
resp = rugbrain_search_multi(query="USDC depeg", collections=["news_v2","social_intel"], limit=10)
for item in resp["results"][:3]:
    print(item["collection"], round(item["score"], 3), item["content"][:200])
```

Write-up: *"USDC depeg was covered in `news_v2` (score 0.81) and `social_intel`
(score 0.77); both describe the March 2023 depeg."* — collection + score, per claim.
Never write "RugBrain says X" without them.

### Addresses and entities — do not use semantic search
```
rugbrain_scam_check(address="0x…", chain="ethereum", limit=10, tier="free")
rugbrain_analyze(address="0x…", chain="ethereum")        # full 3-pillar
rugbrain_wallet_profile(address="0x…", max_hops=2)
rugbrain_scam_radar(query="0x… or BONK or pump and dump", limit=10)
```
`scam_check` is the cheap first look; `analyze` is the full picture. A vector hit on an
address string is not an address lookup — use the typed tools.

---

## 2. Asking for knowledge, not vibes

| Question shape | Tool |
|---|---|
| "What happened / what was reported" | `rugbrain_search_multi` over `news_v2`, `social_intel` |
| "Is this address/token known bad" | `rugbrain_scam_check` → `rugbrain_analyze` |
| "What is the mood on $TICKER" | `rugbrain_social_sentiment(query=…, limit=10)` (social first, then news) |
| "Token address sentiment" | `rugbrain_sentiment(...)` — address-keyed, different tool |
| "What is happening in crypto right now" | `rugbrain_market_brief(query="", limit=10)` |
| "Recent alerts" | `rugbrain_alerts(minutes=60)` |
| "News, filtered" | `rugbrain_news(...)` |

`rugbrain_market_brief()` fuses `news_v2` + `social_intel` + `education`. An empty
`query` is rewritten server-side to a general market query — the returned items still
carry `collection` and `score`. Pass a topic when you want focus.

---

## 3. Technical analysis (TA)

**There is no `rugbrain-mcp` TA tool.** The TA substrate is reachable only over HTTP:

```
GET /api/v1/ta/signals?chain=<chain>&pair=<pool address>&hours=168
GET /api/v1/ta/stats
```

`hours` is clamped to 24–8760 (default 168 = one week of hourly candles). The response
carries evaluated signals plus trend state; each signal has `kind`, `confidence`,
`severity`, `as_of`, `pair`, `summary`, `evidence`.

```
curl -s "http://127.0.0.1:8012/api/v1/ta/signals?chain=solana&pair=<pool>&hours=168" \
  -H "x-api-key: $RMI_RAG_API_KEY"
```

Worked example: a pair with too few candles returns an **empty signal list, not an
error** — report "no TA signal in the last 168h", never "the token is safe".
Confirm the auth header name against `src/rugbrain/app/` before scripting this `[REVIEW]`.
What TA cannot do is tracked in `docs/TA-CAPABILITY.md` (**PENDING**).

---

## 4. Reading market / prediction-market knowledge

**Honest status: there is no verified prediction-market retrieval tool.**
`rugbrain_market_brief` reads news, social and education — it does not read odds,
order books or venue probabilities. Do not answer a prediction-market question from a
`news_v2` hit and present it as market data.

What you may use today:
```
rugbrain_market_brief(query="Bitcoin", limit=10)
rugbrain_social_sentiment(query="election odds", limit=10)
rugbrain_news(...)
```
Before reporting a market collection as broken, read
[`DECISION-EMPTY-MARKET-COLLECTIONS.md`](DECISION-EMPTY-MARKET-COLLECTIONS.md) — some are
empty on purpose. Dataset survey: [`MARKET-DATASETS-2026-10-23.md`](MARKET-DATASETS-2026-10-23.md).
Live odds belong to the chain/market tooling, not to RugBrain.

---

## 5. Citing freshness

Every answer must carry three things: **collection, score, and when the data was seen.**

1. **Ask for the age.** Search results carry a payload timestamp; `rugbrain_health(full=True)`
   and `rugbrain_vector_landscape()` give per-collection counts and readiness.
2. **Quote the date, not the mood.** "As of 2026-10-24" beats "recently". If the payload
   has no timestamp, say the item is undated — do not infer one from rank.
3. **Staleness is checkable:** `python3 scripts/check-collection-staleness.py`.
4. **Dated docs are snapshots.** `docs/COVERAGE-AUDIT-2026-09-28.md` is evidence at
   2026-09-28, not a live metric. Cite it with its date.
5. **Never launder a ranking into a fact.** A 0.62 score in `news_v2` is a relevant
   article, not a confirmed event. Name the source; let the reader weigh it.

---

## 6. Failure modes that look like answers

| Symptom | What it means |
|---|---|
| `results: []`, HTTP 200 | Nothing indexed for that query, or wrong collection. Not "false". |
| Scores clustered ~0.5 | Query and corpus do not match; rephrase or switch tool. |
| First call takes 38–45s | Cold embedder lane (`ARCHITECTURE-V2.md` §0.1). Retry once; do not conclude the service is down. |
| `scam_patterns` returns little | `scam_patterns_8b` is the 4096-dim lane; see `docs/DUAL_TIER_RAG.md`. |
| TA route 422 | `hours` outside 24–8760 or `pair` missing. |
| You need a symbol, a function, a PoC | Wrong system — go to ContextCrypto. |

---

## 7. Two calls to remember

```
rugbrain_health()                                  # is it alive
rugbrain_search_multi(query=…, collections=[…])    # what do we know
```
Everything else is a specialization of those two.
