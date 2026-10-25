# Corpus: what market data we hold, and what to ingest next

**Measured:** 2026-10-25 (ClickHouse `rmi-clickhouse`, `clickhouse-client`, not docs).
**Scope:** the data a market/TA/prediction analysis needs. News is out of scope on purpose —
we are not short of headlines, we are short of *labelled series*.

## 1. Coverage today (verified counts)

| dataset | where | rows | span | freshness | gaps |
|---|---|---|---|---|---|
| `wallet_memory.dex_ohlcv_hourly` | CH | **2,813,171** | 2026-07-30 13:00 → 2026-10-24 20:00 | **17 h** | 53,413 pairs but **42,953 (80%) have <24 candles**; 19 EVM chains, **zero Solana**; Base stale 40 h; blast/fraxtal 14 d |
| `rugdex.ohlcv_1h` | CH | 31,132 | 2026-07-08 → 2026-07-29 | **2,110 h (~88 d)** | 575 tokens, abandoned; aggregate-state columns only |
| `rugdex.ohlcv_5m` | CH | 187,342 | 2026-07-08 → 2026-07-29 | **126,609 min** | same 575 tokens, dead |
| `wallet_memory.binance_klines` | CH | 86,400 | 2026-09-01 → 2026-09-30 | 25 d | 2 symbols (BTCUSDT/SOLUSDT), **one month**, 1-min |
| `wallet_memory.pm_markets` | CH | **990** markets | asof 2026-10-23 07:34 → 2026-10-25 13:45 | **0 h** | **snapshot only — no odds time series, no resolution/outcome columns**; `closed` all false |
| `market_data` (DefiLlama TVL) | Qdrant | 1,062 pts | — | 🟡 unverified | point snapshots, no series; not in CH |
| `market_health_8b` | Qdrant | 636 | — | 2026-10-17 | narrative docs, not series |
| TA docs / substrate | repo | 10 modules, 93 tests | — | live | `ta/reader.py` reads CH; **no feature store** |
| labelled forward returns / signal outcomes | — | **0** | — | — | **does not exist anywhere** |

Chain mix (rows): base 1.15 M · ethereum 638 k · polygon 238 k · optimism 235 k · bsc 126 k ·
avalanche 121 k · arbitrum 86 k · gnosis 83 k · rest <35 k. **Solana: none.**

**Honest read:** we have a broad-but-shallow EVM hour-candle sample (~86 days, 3 months max)
and no labels. Every predictive claim is currently unbacked — see
`docs/CAPABILITY-GAPS-JOURNALISM-TA.md` §3.

## 2. Candidates (5)

| # | candidate | licence / ToS | volume | refresh path | unlocks | verdict |
|---|---|---|---|---|---|---|
| 1 | **Binance public data** `data.binance.vision` (klines + trades) | No licence published; Binance ToU — free download, **no redistribution** → `internal_only` (already the stance in `dataset_licences`) | all spot pairs, 1m/1h since 2017; SOLUSDT monthly 1m ≈ 43 k rows | monthly zip + daily increment; `scripts/ingest_binance_klines.py` exists, `ReplacingMergeTree(symbol, open_time)` | labelled forward returns, walk-forward backtests, calibrated signal classifier, honest baseline | **ACCEPT** (internal) |
| 2 | **Polymarket Gamma + CLOB** (`prices-history`) | Public API, no key; no explicit redistribution grant → store internally, attribute, never republish | 990 markets known; per outcome token, full odds history since creation (hourly/minute) | free poll per `clobTokenId`; idempotent `ReplacingMergeTree(market_id, token_id, ts)` | **odds calibration** (Brier, fav-longshot bias), event-study fusion with TA regime | **ACCEPT** (internal) |
| 3 | CoinGecko `/coins/{id}/ohlc` | Free tier: 30 calls/min, attribution, **no redistribution**; paid for commercial | 365 d hourly max on free tier; coarse buckets | rate-limited polling | spot context only | **REJECT as corpus** — cannot build history, cannot store; on-demand read at most |
| 4 | DexScreener | **No published ToS/licence for bulk storage**, undocumented endpoints | unbounded but unstable | scrape | — | **REJECT** — no lawful storage basis |
| 5 | Dune decoded tables (we pay) | Dune ToS: query results may be displayed with attribution; **bulk re-hosting of raw tables not permitted** | large | `run_query` spends credits | ad-hoc chain questions | **REJECT as corpus sink** — use live for questions, do not mirror |
| 6 | Coinbase Exchange `/products/{id}/candles` | Exchange ToU, no bulk dump, 300 req/10 s | 300 candles/req, paginated | polling | USD venue | **REJECT** — rate-limited, ToS-restricted; Binance covers the same need lawfully |
| 7 | Third-party "labelled TA / returns" datasets (Kaggle et al.) | mixed CC0/CC-BY, but **methodology unverifiable**, survivorship-biased pair selection | small | static | none we cannot compute ourselves | **REJECT** — labels must be ours, on licensed prices, or the backtest is unfalsifiable |

## 3. Top 2 — bounded ingest plans

### 3.1 Binance klines → the label engine (do this first)

- **Universe (bounded):** 40 symbols — BTC/ETH/SOL/BNB/XRP/DOGE/ADA/AVAX/LINK + the USDT
  pairs we already see in `dex_ohlcv_hourly`; horizons 1 h and 1 m.
- **Window:** 2019-01-01 → now in **90-day chunks** (3 monthly calls per chunk), starting
  2023-01-01 for 1 m and 2019-01-01 for 1 h (1 m is ~5 GB uncompressed per 40 symbols/year).
- **Schema:** `wallet_memory.binance_klines` as-is — `ReplacingMergeTree (symbol, open_time)`,
  12 canonical columns, timestamps normalised µs→DateTime64.
- **Provenance:** one `dataset_licences` row per (table, symbol-range, month-batch):
  `licence='none published'`, `licence_status='internal_only'`
  `redistribution='do not redistribute'`, `source_url=data.binance.vision`, `rows`, `period_*`.
- **Dedupe:** `ReplacingMergeTree` keyed on `(symbol, open_time)`; ingest asserts
  `uniq(symbol, open_time) == count()` post-load; re-running a month is a no-op.
- **Cadence:** daily TTL-less increment at 04:10 UTC (previous UTC day file), monthly
  reconciliation on the 3rd. Idempotent, flocked, cached under `/srv/data/raw/binance/`.
- **Unlocks:** the **labelled forward-return dataset** (1 h / 4 h / 24 h, neutral band dropped)
  and a materialised feature table (EMA/RSI/ATR/vol/volume-delta, causal), which is exactly
  the missing prerequisite for `signal-classification` + `walk-forward-validation`.

### 3.2 Polymarket odds history → the calibration set

- **Universe:** the 990 markets in `pm_markets` plus any open market with `volume > $50 k`;
  one row per `(market_id, clob_token_id, ts)`.
- **Schema (new):** `wallet_memory.pm_odds_history` — `ReplacingMergeTree(market_id, token_id, ts)`;
  columns `market_id, condition_id, token_id, outcome, ts, price Float64, source Enum8('clob','gamma'),
  ingested_at`. Resolution side table `pm_resolutions(market_id, resolved_outcome, resolved_ts)`.
- **Provenance:** `dataset_licences` row `polymarket_api`, `licence='public API, no redistribution grant'`,
  `licence_status='internal_only'`, `redistribution='attribute, do not republish'`.
- **Dedupe:** `ReplacingMergeTree` on the 3-tuple + a per-poll `max(ts)` watermark so each poll
  fetches only the missing tail; `FINAL` on read.
- **Cadence:** hourly for open markets (≈ a few hundred `prices-history` calls, free), daily
  reconciliation pass for closed markets' final 24 h + resolution.
- **Unlocks:** **calibration of prediction odds** — Brier / log-loss vs base rate, fav-longshot
  bias, and an event-study join ("did our `dump_pattern` signal precede a ≥10 pt odds move?").

## 4. Rejected, and why (one line each)

- **CoinGecko / DexScreener / Coinbase** — no lawful or technically feasible *storage* basis
  (no redistribution, undocumented, or rate-capped to uselessness). Read on demand only.
- **Dune decoded tables** — paying for query access is not a licence to mirror tables;
  keep Dune as a live question tool, never a corpus sink.
- **Third-party labelled TA sets** — unverifiable labelling and pair survivorship; labels
  computed on our own licensed prices are the only falsifiable version.

## 5. First analysis I would run (after 3.1 lands)

**Claim:** a 12-feature causal GBDT does **not** beat the naive "always up" / momentum-24 h
baseline on BTCUSDT+ETHUSDT+SOLUSDT 1 h bars, 2023→2026.

- Rolling walk-forward: train 720 h, test 168 h, step 168 h, **embargo = horizon**,
  purge train labels overlapping the test window (`walk-forward-validation`).
- Labels: binary ±1% at 4 h, neutral dropped; `isotonic` calibration on validation folds only.
- Metric gate: out-of-sample AUC, precision@top-decile, and net-of-fee return vs both baselines;
  a non-beating model is reported as *"regime + anomaly only"*, per the editorial rule.
- Then the Polymarket leg: are our regime labels predictive of odds drift (event study, not a bet)?

**Deliverable:** one notebook/report with the fold table and the baseline comparison — no
"predictor" ships until that table exists.
