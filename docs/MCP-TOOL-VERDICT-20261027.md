# MCP tool verdict — 2026-10-27

Every claim below is a live measurement against `127.0.0.1:8012` /
`:9104` on Talos, not a reading of the code.

## 1. Scam check: similarity is not evidence about an address

`/api/v1/rag/v2/scam-check` graded blue chips `high` because it ran a semantic
search over collections in which *every* row is a scam record. Cosine
similarity to the word "scam" is not evidence about a contract, and five of the
five hard-coded collection names (`known_scams`, `scam_patterns`,
`threat_intel_v2`, `rmi_community`, `wallet_v2`) no longer exist — the data
moved to the `_8b` twins.

Fix: `scam_verdict.decide_verdict` grades only rows whose `metadata.address`
equals the queried address, plus a verified blue-chip registry
(`blue_chips.py`) that supplies the *reason* for a `low` verdict.

Live, `tier=pro`, rebuilt container:

| token | address | risk | conf | reason |
| --- | --- | --- | --- | --- |
| USDC | 0xA0b8…eB48 | low | 80 | verified_blue_chip:USDC:Circle |
| USDT | 0xdAC1…1ec7 | low | 80 | verified_blue_chip:USDT:Tether |
| DAI | 0x6B17…1d0F | low | 80 | verified_blue_chip:DAI:Sky/MakerDAO |
| WETH | 0xC02a…6Cc2 | low | 80 | verified_blue_chip:WETH:Wrapped Ether |
| WBTC | 0x2260…C599 | low | 80 | verified_blue_chip:WBTC:BitGo |
| UNI | 0x1f98…F984 | low | 80 | verified_blue_chip:UNI:Uniswap |
| AAVE | 0x7Fc6…DaE9 | low | 80 | verified_blue_chip:AAVE:Aave |
| LINK | 0x5149…86CA | low | 80 | verified_blue_chip:LINK:Chainlink |
| scam 1 | 0xb262…7d77 | high | 90 | address_label_high (2 scoped rows) |
| scam 2 | 0xd8db…07b0 | high | 90 | address_label_high (2 scoped rows) |
| scam 3 | 0x502e…ed1d | high | 90 | address_label_high (2 scoped rows) |
| unlabelled | 0x0000…dEaD | unknown | 0 | no_address_scoped_evidence |

`unknown` is the honest default: "no address-scoped evidence" is not clean.

## 2. Boundary table: the _8b twins were not all allowlisted

`PUBLIC_COLLECTIONS` allowed `news_v2_8b` but not `news_articles_8b`,
`social_intel_8b`, or `education_8b`. Result: `rugbrain_search_multi` and both
market tools answered `403 collection_not_available` for the collections that
are actually fed. All three now return `200` with rows; the collections hold
2,451 / 25,578 / 6,036 points (the bare names hold 24 / 1 / 0).

## 3. `rugbrain_search(news_v2)` dying at ~57 s — root cause

Not a code fault and not a Qdrant fault. The stdio child was being **killed by
the unit's memory ceiling**:

* `--stateful` was removed from `mcp-http-rugbrain.service` on 2026-10-25
  16:59. In stateless mode supergateway spawns a **fresh fastmcp child per
  HTTP request** (journal: one `Starting MCP server` + `auto-initialize` per call).
* One child measures **108.8 MB at import**, ~125 MB resident; the node bridge
  is ~80 MB.
* The unit's budget class (`99-mcp-budget.conf`) was sized from
  `measured_peak=240MB` — a number that did **not** include the child — and
  clamped `MemoryMax=384M` / `MemoryHigh=326M`.
* Three concurrent calls therefore need ~375 MB, i.e. the cap. The kernel then
  kills the children mid-call: `Memory cgroup out of memory: Killed process
  (fastmcp) anon-rss:514400kB`, and supergateway logs only
  `Child exited: code=null, signal=null`. The caller sees the tool die with no
  Python traceback, which is why the traceback never existed.

Fix: keep stateless (stateful was tried and rejected — with `--stateful`, three
concurrent children sat in `D` state and no call returned inside 120 s) and give
the unit headroom for the children it spawns:
`MemoryHigh=768M` / `MemoryMax=1G` via
`mcp-http-rugbrain.service.d/99z-rugbrain-child-headroom.conf`.

Proof, 6 concurrent `rugbrain_search` calls: all `200`, finished in 43 s, unit
`NRestarts=0`, `MemoryPeak=731 MB` of 1 GB. Before the change the same 6 calls
killed the children and restarted the unit.

## 4. Four dead tools — adjudicated

| tool | what it returned | verdict |
| --- | --- | --- |
| `rugbrain_alerts` | `{"count": 0, "alerts": []}` for 24 h | **retired** |
| `rugbrain_social_sentiment` | items with `score: 0.0` | **repaired** |
| `rugbrain_news("")` | scraped page chrome (cookie CSS, JSON-LD) | **repaired** |
| `rugbrain_market_brief` | 4/5 near-duplicate items | **repaired** |

* **alerts** — `/api/v1/alerts/recent` reads a *per-process* in-memory store
  (`realtime_alerts._store`) that only a detector loop inside the API process
  can fill; the MCP facade is a different process, so the tool could only ever
  answer "no alerts". Its one Qdrant-backed alternative `/api/v1/alerts/live`
  reads `live_threats`, which holds **0 points**. No signal to repair →
  removed, with the reason recorded in `tools_intel.py`.
* **social_sentiment** — two defects: the copies read `item["score"]`, which
  the API never emits (it emits `rerank_score` / `rrf_score` / `similarity`),
  hence 0.0 for every item; and it searched `social_intel` (1 point) instead of
  `social_intel_8b` (25,578). Now labels each row with its own source
  collection and returns real scores.
* **news("")** — `feed/all` queried `threat_intel` (does not exist) and
  `youtube_videos` (never created), and had no content gate, so a generic query
  returned nav menus and `visibility: hidden` CSS as "news". Now queries
  `news_articles_8b` / `podcast_episodes` / `newsletters` and filters every row
  through `result_quality.is_publishable_news`.
* **market_brief** — three sequential reranked searches (3 × 8-15 s) plus no
  dedupe: items repeated across `news_v2` / `social_intel` / `education`, and
  the third leg hit the 30 s timeout (`MCP server process failed`). Now runs
  the legs concurrently without rerank (see `RERANK-VERDICT.md`) and dedupes by
  normalized content. Live: 5 items, 5 distinct stories, non-zero scores.
