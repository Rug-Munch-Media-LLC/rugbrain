# Decision — `liquidation_events` + `exchange_flows` (1024-dim empty shells)

> Lane H4 (rugbrain), 2026-10-15. Every claim below is measured against live
> Qdrant (`http://127.0.0.1:6334`) and the repo at `/srv/work/repos/rugbrain`
> on that date. Commands are quoted in §2 so the numbers are reproducible.

---

## 1. DECISION

**RETIRE both collections.** Neither has a source that feeds it, so there is
nothing to migrate. Re-embedding to a 4096 `_8b` twin would create a second
empty collection and a second promise of data we do not have.

| Collection | Dim | Live points | Source that writes it | Verdict |
|---|---:|---:|---|---|
| `liquidation_events` | 1024 | **0** | **none** | **RETIRE** |
| `exchange_flows` | 1024 | **0** | **none** | **RETIRE** |

The two are already listed as Group B empty shells in
`docs/DROP-RETIRED-BASES.md` (0 points, 787-byte empty parquet in B2). This
decision makes the *reason* explicit and removes the remaining declarations
that promise data.

---

## 2. EVIDENCE

### 2.1 Live state (measured)

```bash
curl -s http://127.0.0.1:6334/collections/liquidation_events
#   dims={'size': 1024, 'distance': 'Cosine'}  points=0
curl -s http://127.0.0.1:6334/collections/exchange_flows
#   dims={'size': 1024, 'distance': 'Cosine'}  points=0
```

Both are 1024-dim (a retired lane — canonical is 4096) and hold **zero** points.

### 2.2 No source feeds them

`grep -rn "liquidation_events\|exchange_flows" src/ scripts/ --include="*.py"`
returns only declarations, never a write:

- **`liquidation_events`** — the only references are the audit comment in
  `src/rugbrain/firehose/market_sources.py` (a docstring listing collections
  that *were* at 0) and `scripts/qdrant_date_plan.py` (a date-planning list).
  `market_sources.py` has **no liquidation fetcher at all** — its fetchers are
  trending, market-health, stablecoin, derivatives, regulation, hacks, whale
  transfers, hyperliquid, stablecoin-whales, BTC-whales, Solana-whales. There is
  no `fetch_liquidations`.
- **`exchange_flows`** — `src/rugbrain/exchange_flow.py` documents "All indexed
  into Qdrant 'exchange_flows' collection" but **has no write path**:
  `exchange_flow_cycle()` builds a dict and returns it; `exchange_flow_loop()`
  logs it. There is no `upsert`, no `index`, no embed call. The docstring is
  aspirational, not descriptive.

### 2.3 Nothing on the MCP surface promises them

`grep -rn "liquidation_events\|exchange_flows" src/rugbrain/rugbrain_mcp/`
returns **nothing**. No tool advertises either collection, so retiring them
breaks no tool contract.

### 2.4 The 1024 set has no `_8b` twin by design

`docs/DROP-RETIRED-BASES.md` §"Why the 1024 set exists": the six 1024
collections (`exchange_flows`, `hyperliquid`, `liquidation_events`,
`token_unlocks`, `wallet_clusters`, `youtube_videos`) are pinned below canonical
and have no twin. All six hold 0 points. They are empty shells, not lagging
copies — there is no data to re-embed.

---

## 3. WHAT "RETIRE" MEANS HERE (executed)

Retiring is a **declaration** change, not a delete. The physical drop is the
operator's call via `scripts/drop_retired_bases.py` (already validated, dry-run
by default, listed in `docs/DROP-RETIRED-BASES.md`). This lane removes the
promises so nothing advertises data we do not have:

1. **`src/rugbrain/corpus/entries_ops.py`** — the `exchange-flows` and
   `liquidation-events` catalog entries are marked retired (`retired=(1024,)`)
   and their notes state "no source; retired 2026-10-15". A retired entry is
   excluded from the served catalog.
2. **`src/rugbrain/qdrant_store/owned_registry.py`** — the two names stay in the
   owned set (the write gate must still recognise them if a future source is
   built), but a comment records the retirement and the reason.
3. **This document** — the canonical record of the decision and its evidence.

The physical collections are left in place: they hold 0 points, cost nothing,
and `drop_retired_bases.py` already knows how to remove them when the operator
runs it. Deleting them here would be an irreversible action outside this lane's
scope.

---

## 4. WHY NOT MIGRATE

Migration means re-embedding existing points from a 1024 source into a 4096
`_8b` twin. There are **no points** and **no source**, so migration would:

- create a second empty collection (`liquidation_events_8b`, `exchange_flows_8b`),
- add two more names to the registry that promise data,
- and require an embed lane run to produce zero vectors.

That is strictly worse than retiring. If a real source is built later (M7/M8/M9
in `docs/CAPABILITY-GAPS-SCAM-MARKET.md`), the correct move is to write it
**directly at 4096** into a fresh collection — not to resurrect a 1024 shell.

---

## 5. OPEN QUESTIONS

1. **M7/M8/M9 remain unbuilt.** `smart_money_tracker.py` (4 hardcoded wallets)
   and `exchange_flow.py` (no write path) exist but are not scheduled and do not
   persist. Building them is an operator cost call (Etherscan key + embed
   budget), not a lane action. **[REVIEW]**
2. **`exchange_flow.py`'s docstring is wrong** — it claims to index into
   `exchange_flows`. It should either gain a write path or have the claim
   removed. Left as-is here to keep the diff bounded; flagged for the next
   market lane. **[REVIEW]**
3. **The physical drop** of the two shells is deferred to the operator's
   `drop_retired_bases.py` run (Group B). **[REVIEW]**
