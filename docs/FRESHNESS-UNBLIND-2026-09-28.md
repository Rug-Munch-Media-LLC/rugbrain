# FRESHNESS-UNBLIND — what the corpus can and cannot say about its own age

**Lane S · 2026-09-28 · measured against live Qdrant :6334 (v1.19.0), read-only**

## The point

A system that cannot state the age of two thirds of itself will serve stale
intelligence silently, and nobody will know. This report makes every
collection answer, or be named as unable to answer.

## Headline

| | collections | points | share of mass |
|---|---:|---:|---:|
| **MEASURED** (ingest timestamp found, age is real) | 15 | 1,127,513 | 18.1% |
| **UNMEASURABLE** (no ingest timestamp) | 81 | 5,089,050 | 81.9% |
| **Total** | **96** | **6,216,563** | 100% |

**81.9% of the corpus by mass cannot state its own age.** The previous check
reported 61 collections and could not measure 48. All 96 are now classified,
and the unmeasurable set is no longer a silent omission — it is a named list
with a per-item reason and a stated cost to fix.

Of the 81 unmeasurable: **54 have no ingest field** (5,089,050 pts),
**21 are empty** (0 pts, declared but never fed), **6 are served from another
store** (Neo4j — not a Qdrant gap).

## Four-way split

Thresholds: FRESH ≤ 7d, AGING ≤ 14d, STALE > 14d. Both boundaries sit in the
EMPTY gap between the measured age clusters (0-4d alive, 17d+ dead), so no
collection's verdict depends on a round number.

### Fresh (11) — measured, recently ingested
| collection | points | age | ingest field | coverage |
|---|---:|---:|---|---:|
| rag_documents | 530,817 | 0d | created_at | 101/250 |
| rag_documents_8b | 530,843 | 0d | created_at | 101/250 |
| live_threats | 11,164 | 1d | metadata.detected_at | 250/250 |
| live_threats_8b | 11,164 | 1d | metadata.detected_at | 250/250 |
| security_alerts | 125 | 1d | metadata.detected_at | 125/125 |
| social_intel | 20,984 | 2d | detected_at | 19/250 |
| agent_memory | 4 | 3d | created_at | 4/4 |
| social_intel_8b | 20,980 | 3d | detected_at | 15/250 |
| newsletters | 17 | 4d | metadata.ingested_at | 17/17 |
| podcast_episodes | 26 | 4d | metadata.ingested_at | 26/26 |
| security_alerts_8b | 119 | 4d | metadata.detected_at | 119/125 |

### Aging (0) — none
No collection lands between 5d and 16d. This is the empty gap the thresholds
are built on, and it is why one threshold is safe for every collection.

### Stale (4) — measured, and dead
| collection | points | age | ingest field |
|---|---:|---:|---|
| hacks_v2 | 8 | 29d | metadata.ingested_at |
| hacks_v2_8b | 8 | 29d | metadata.ingested_at |
| market_health | 627 | 17d | metadata.ingested_at |
| market_health_8b | 627 | 17d | metadata.ingested_at |

These are **real dead feeds with real evidence**: `metadata.ingested_at` is
present on 8/8 and 250/250 scanned points respectively, so this is measured
staleness, not blindness. `hacks_v2` last ingested 2026-08-30 — 29 days ago.
Both are small by mass, which is exactly why blindness elsewhere is easy to
miss: the loudest failure is 1,270 points, and the silent one is 5 million.

## The unmeasurable list — the real output

### A. No ingest field (54 collections, 31 distinct corpora, 5,089,050 pts)

Every one of these was sampled up to 250 points (or all points, for small
collections) and **no field naming an ingester action was found**. Grouped by
corpus; the `_8b` twin is a second collection with the same blind spot.

| corpus | points | cols | what would measure it |
|---|---:|---:|---|
| **codebase_reference_v2** | 4,257,472 | 2 | stamp `ingested_at` in the code-embed writer. Largest collection in the fleet and fully blind. |
| news_v2 | 325,385 | 2 | stamp on fetch. `ts` is present but is a **content date** — see below. |
| scam_intel_v2 | 215,756 | 2 | stamp in the scam-intel pipeline entry point. |
| threat_intel_v2 | 83,728 | 2 | stamp in the threat sweep. |
| known_scams | 53,440 | 2 | stamp in the Etherscan/PhishDestroy loaders. |
| scan_intel | 47,240 | 1 | stamp in the scanner writer. |
| defi_exploits | 33,296 | 2 | stamp on ingest; `due_date` is agent-stamped, not ingest. |
| scam_patterns | 14,204 | 2 | stamp in the miner. |
| regulation_enforcement | 10,162 | 2 | stamp on fetch. |
| crypto_vulnerabilities | 9,836 | 2 | stamp on ingest. |
| master_skills | 8,990 | 2 | stamp in the skill-index build. |
| education | 8,139 | 2 | stamp on fetch. |
| news_articles | 4,728 | 2 | stamp on fetch. |
| security_patterns | 4,016 | 2 | stamp in the pattern writer. |
| exploits | 2,688 | 2 | stamp on ingest. |
| contract_security | 2,198 | 2 | stamp on ingest. |
| rug_events | 1,877 | 1 | stamp on ingest. |
| contract_audits | 1,804 | 2 | stamp in the audit collector. |
| security_feeds | 1,716 | 2 | stamp per feed poll. |
| stablecoin_market | 672 | 2 | ClickHouse-backed; stamp on the CH write. |
| market_data | 467 | 1 | ClickHouse-backed; stamp on the CH write. |
| github_audits | 398 | 2 | stamp in the GH collector. |
| research_v2 | 312 | 2 | stamp on fetch. |
| agent_vectors | 182 | 1 | stamp in the agent-vector build. |
| security_patterns_test | 181 | 2 | scratch collection; ignore. |
| protocol_design | 116 | 2 | stamp on fetch. `date` is a **revision date** — see below. |
| cross_chain | 26 | 2 | stamp on ingest. |
| agent_memory_entities | 14 | 1 | stamp on write. |
| privacy_protocol | 4 | 1 | stamp on fetch. |
| public_crypto_patterns | 2 | 1 | stamp on ingest. |
| scanned_tokens | 1 | 1 | single point; stamp on write. |

### B. Empty — declared, never fed (21 collections, 0 pts)

`audit_findings`, `bridge_architecture`, `coding_knowledge`, `crime_reports`,
`does_not_exist`, `exchange_flows`, `hyperliquid`, `l2_risk`,
`liquidation_events`, `mem0migrations`, `mev_architecture`, `scam_signatures`,
`scams_v2`, `scan_intel_8b`, `solana_patterns`, `sp_tailtest`, `standards`,
`token_unlocks`, `youtube_videos`, `zk_circuits`, `agent_vectors_8b`.

These cannot be stale because they have never been fed. They are a different
finding: a declared corpus that does not exist. `does_not_exist` and
`sp_tailtest` are scratch/test names and are excluded from the gate.

### C. Served from another store — NOT a Qdrant gap (6 collections, 0 pts here)

`wallet_profiles`, `wallet_profiles_8b`, `wallet_clusters`, `wallet_v2`,
`wallet_v2_8b`, `scanner_memory` — declared `internal://neo4j/wallet-labels`
in `corpus/entries_scam.py`.

**These are not stale.** The wallet-label corpus is fed by a Neo4j pipeline
that this Qdrant probe never reads. Reporting them as stale would be a false
alarm on a corpus that is fed from a store we are not looking at — and a
report that cries wolf is worse than no report, because it teaches people to
ignore it. Each is reported `off_qdrant` with its declared home. **Their real
freshness is still unmeasured** — that requires a Neo4j-side check, which is
named below as work not done.

## How ingest time is distinguished from content date

This is the part that has to be right or the other numbers are worthless.

A **content date** is a fact about the world ("the exploit happened on the
9th"). An **ingest time** is a fact about the corpus (stamped by the ingester
when the point was written). Only the second answers "is this feed dead".

`freshness_classify.classify_field` is the single decision point:

- **Ingest fields** (allowlist): `ingested_at`, `indexed_at`, `ingest_ts`,
  `detected_at`, `first_seen`, `seen_at`, `loaded_at`, `created_at` — every
  one names an event the INGESTER performed on arrival.
- **Content fields, never read as ingest**: `published`, `date`, `ts`,
  `event_date`, `year`, `exploit_date`.
- **Agent-stamped**: `due_date` — when a judgement was made, not when the
  corpus was fed.
- A field outside both lists is `UNKNOWN` and never read.

**A content date is never promoted, not even for a document-shaped corpus.**
An earlier version of this module accepted `ts`/`date` as an ingest proxy for
news and research. Measured against live Qdrant, that was wrong in both
directions and has been removed:

- `protocol_design` — 58 architecture documents whose `date` is a **revision
  date**. As ingest time it reported the corpus 13d "aging", which said
  nothing about whether the feed ran.
- `news_v2` — 162,476 points carrying `ts`. The verdict **flipped between two
  consecutive runs on a stable collection** (unmeasurable → fresh) purely on
  which points a 250-point sample happened to reach. A verdict that moves for
  a reason unrelated to the feed is not a measurement.

Both are now unmeasurable, which is the honest answer. The cost is two more
gaps; the benefit is that no number in this report moves for a reason that is
not about a feed.

`parse_timestamp` additionally refuses any stamp more than 2 days ahead of
now. Measured: `rag_documents` and `defi_exploits` both carry content dates of
**2026-12-08 — eleven days in the future** — which a naive content-date check
reads as maximally fresh. A 2026-12-08 exploit has not happened yet.

## Correction to the prior measurement

`known_scams` was previously reported as "26,720 points, 29 days stale". Direct
inspection shows **it carries no time-like field at all** — a 150-point payload
scan found no date, timestamp or ingest field of any kind. The 29-day figure
came from a content date, not an ingest time. `known_scams` is **unmeasurable**,
not stale. It is in section A above.

`hacks_v2`, by contrast, is genuinely 29 days stale: its
`metadata.ingested_at` reads 2026-08-30 on 8/8 points. Same number, opposite
meaning — which is the whole point of the distinction.

## What could not be measured, and why

1. **The Neo4j wallet-label corpus.** Declared, identified, and correctly
   reported `off_qdrant` — but its actual age is unmeasured. Needs a Neo4j-side
   probe reading the same ingest-field contract. Not done here.
2. **Blind collections under 250 points.** For collections with more than 250
   points the verdict is "no ingest field in a 250-point sample", not "no
   ingest field anywhere". A rare field could exist. The blind-mass figure
   (81.9%) is therefore a floor, not a ceiling.
3. **`_8b` twins are counted separately.** They hold 2,517,126 of the 5,089,050
   blind points — 49.5% of the blindness is duplication of a corpus that is
   already blind. Distinct-corpora blindness is 31 corpora, not 54 collections.
4. **ClickHouse-backed market data** (`market_data`, `stablecoin_market`) is
   present in Qdrant as a cache with no ingest field. Their home is CH; a
   Qdrant-side gap is not evidence about CH. Reported blind, home not asserted.
5. **No history.** Qdrant exposes no per-collection creation or last-write
   time in this build, and no git-tracked ingest ledger exists for these
   corpora. There is no way to date a collection that was never instrumented
   and has no logs. This is the irreducible part of the gap.

## What was deliberately not done

- **No writes to Qdrant.** GET and scroll only. Nothing was deleted,
  re-indexed, or re-ingested to improve any number.
- **No content date used as ingest evidence**, per the reasoning above.
- **No gap closed by assumption.** Every unmeasurable entry names a reason and
  a cost; none carries an invented timestamp.
- The gate still fails on **measured** staleness only (exit 1, currently
  tripped by `hacks_v2` and `market_health`). A permanently-red gate gets
  muted, and a muted gate is worse than none — so the unmeasurable set is
  printed in full on every run and named here, rather than being folded into
  the failure.

## Files

| file | role |
|---|---|
| `src/rugbrain/freshness_transport.py` | read-only Qdrant GET/scroll, payload flattening, timestamp parsing |
| `src/rugbrain/freshness_classify.py` | the ingest-vs-content decision (the single decision point) |
| `src/rugbrain/freshness_report.py` | verdicts, reasons, off-Qdrant table, the `Probe` record |
| `src/rugbrain/collection_freshness.py` | the probe: one verdict per collection |
| `scripts/check-collection-staleness.py` | wired gate — now prints the full unmeasurable set |
| `tests/test_collection_freshness.py` | 15 tests, one per rule above |

Reproduce: `python3 scripts/check-collection-staleness.py --json`
