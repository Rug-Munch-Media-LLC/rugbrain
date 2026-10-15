# Capability Gaps — Journalism, Technical Analysis, Predictor

Status: 2026-10-15 · Lane R4 · author: backend-dev
Scope: what exists, what was wired this lane, and what a *predictor* could
honestly be on our data. No fantasy — every claim below names its evidence.

---

## 1. Journalism — the fact-check gate is now WIRED

### What was broken

`src/rugbrain/factcheck.py` (177 lines, 9 tests) shipped 2026-10-15 as a
deterministic four-step gate (extract → bind → corroborate → verdict). It was
**unwired**, and worse: `src/rugbrain/content_engine.py` line 39 imported
`FactCheckResult, fact_check_gate` — **names that did not exist**. The module
raised `ImportError` on import, so the entire content engine was dead:

```
ImportError: cannot import name 'FactCheckResult' from 'rugbrain.factcheck'
```

The call site `_compose_with_factcheck` existed but was **never called** — the
draft path (`generate_channel_drafts` → `_timed_compose`) called `_compose`
directly, bypassing the gate entirely.

### What was done

| File | Change |
|---|---|
| `src/rugbrain/factcheck_gate.py` (new, 87 lines) | `FactCheckResult` dataclass + `fact_check_gate(text, story)` adapter mapping `check_draft`'s report to a typed verdict (`PASS`/`FAIL`/`SKIP`). Split out so each module stays one job and under 200 lines. |
| `src/rugbrain/content_engine.py` | Fixed the import; replaced the broken 34-line `_compose_with_factcheck` with a correct 20-line version; **wired `_timed_compose` to call it**; added `FACTCHECK_ENABLED` (`RAG_FACTCHECK_ENABLED`, default **ON**). Net **shrink** 343 → 341 lines. |
| `tests/test_factcheck_wiring.py` (new, 5 tests) | Proves the wiring: bad claim → `writer="rejected"`, empty text; good draft → passes; flag OFF → bypass; opinion → pass-through; and that `generate_channel_drafts` routes through the gate. |

### Gate semantics (editorial standards #21, #12, #15)

- **PASS** — ≥1 claim corroborated by ≥2 independent domains. Flagged claims
  are stripped and a `[Fact-check: N claim(s) stripped]` note is appended.
- **FAIL** — claims present, none corroborated → draft blocked
  (`writer="rejected"`, empty text). The bot's existing quality gate already
  drops `writer=="template"`; `"rejected"` is a new, stronger drop.
- **SKIP** — no verifiable claims (opinion) → pass through unchanged.

### Evidence

```
$ python3 -m pytest tests/test_factcheck_wiring.py tests/test_factcheck.py -q
14 passed in 1.28s
```

### Open questions

- **Source breadth.** The gate binds a claim against the *story's own* url +
  any attached `sources`. A story from a single outlet can never reach 2
  independent domains, so it will FAIL. Is that the intent (block
  single-source claims) or should the gate query the corpus for corroborating
  coverage? **Decision needed.**
- **`content_engine.py` size gate.** The file is 341 lines vs a stale baseline
  of 289 — the gate **already failed at HEAD** (343) before this lane. The
  ratchet refuses to raise the baseline, so this is pre-existing debt, not
  introduced here. It needs a real split (the file is a god module).

---

## 2. Technical Analysis — substrate complete, tool surface was missing

### What exists (audited, not assumed)

`src/rugbrain/ta/` is a **complete, tested substrate** (10 modules, 93 tests):

| Module | Job |
|---|---|
| `candles.py` | `Candle` value type + `Series` with `upto()` point-in-time view |
| `ohlcv_sql.py` | SQL text for the OHLCV reads (built, never executed) |
| `reader.py` | ClickHouse read of `wallet_memory.dex_ohlcv_hourly` |
| `indicators.py` | SMA / EMA / RSI / ATR / volume-delta / volatility / drawdown — pure functions, causal by construction |
| `signatures.py` | Three triggers: liquidity collapse, abnormal volume, dump pattern |
| `signals.py` | candles + indicators → scored `Signal`; `trend_state()` |
| `backtest.py` | walk-forward, one-bar fill lag |
| `no_lookahead.py` | leak detector — rewrites the future, checks the verdict moved |

**Data is real and live:** `wallet_memory.dex_ohlcv_hourly` holds
**2,640,471 hourly candles** across 53,935 DEX pairs (verified this lane via
`clickhouse-client`).

### The gap: no tool surface

Nothing in the HTTP API or the MCP facade could reach the TA substrate. This
lane added the first piece:

| File | Change |
|---|---|
| `src/rugbrain/routes/ta.py` (new, 96 lines) | `GET /api/v1/ta/signals?chain=&pair=&hours=` → evaluated signals + trend; `GET /api/v1/ta/stats` → coverage/freshness. Read-only. |
| `src/rugbrain/app/routes_registry.py` | Registered `ta_router`. |
| `tests/test_ta_route.py` (new, 5 tests) | Shape, empty-series, 503 on store-down, 400 on bad window, stats. |

### Evidence

```
$ python3 -m pytest tests/test_ta_route.py -q
5 passed in 0.98s
$ python3 -m pytest tests/test_ta_indicators.py tests/test_ta_signals.py \
    tests/test_ta_no_lookahead.py tests/test_ta_reader.py -q
93 passed in 1.57s
```

### Tool surface plan (next)

1. **MCP tool** `rugbrain_ta_signals(chain, pair, hours)` in
   `src/rugbrain/rugbrain_mcp/server.py`, proxying the new HTTP route — the
   facade pattern every other tool uses.
2. **Batch scan** `GET /api/v1/ta/scan?chain=&limit=` over `top_pairs()` — the
   "what is dumping right now" surface the alert dispatcher can consume.
3. **Wire signals into the alert path** — `alert_dispatcher` currently has no
   TA input; a `dump_pattern` signal at `critical` is exactly an alert.

---

## 3. Predictor — what is honestly possible on our data

**Verdict: a price-direction predictor is NOT supportable today. A market-regime
classifier and an anomaly detector ARE.**

### Why a price predictor is not honest yet

- **No labelled forward-return dataset.** `dex_ohlcv_hourly` gives us candles,
  but a supervised classifier needs forward-return labels per pair per horizon,
  with a walk-forward split. That is buildable (the `signal-classification`
  skill's `create_binary_labels` is the recipe) but it does not exist.
- **Survivorship + liquidity bias.** 53,935 pairs, most with thin coverage. A
  model trained on all of them learns "dead pairs stay dead", not signal.
- **No feature store.** Indicators are computed on demand; there is no
  materialised feature table to train against.
- **Honest bar:** a predictor must beat a naive baseline (always-up, or
  momentum) out-of-sample on a walk-forward split, with the embargo the
  `signal-classification` skill mandates. Until that harness exists, any
  "prediction" is a number without a track record.

### What IS supportable now

1. **Market-regime classification (unsupervised, no labels needed).**
   `trend_state()` already emits bull/bear/flat from an EMA cross. Extend to a
   regime label per pair from existing indicators (trend + realised volatility
   + volume regime) — descriptive, not predictive, and honest about it.
2. **Anomaly detection (the signatures already are this).**
   `liquidity_collapse` / `abnormal_volume` / `dump_pattern` are anomaly
   detectors with confidence scores. They are the *right* first product: they
   describe an observed state, not a forecast.
3. **Corpus-level regime read.** `market_health_8b` (627 docs) + `market_data`
   (467 DeFiLlama TVL snapshots) + `stablecoin_market_8b` (336) can support a
   *narrative* regime read (risk-on / risk-off) fused with the TA regime — a
   retrieval product, not an ML model.

### The honest path to a predictor (if pursued)

1. Materialise a feature table from `dex_ohlcv_hourly` (indicators per pair per
   hour) — a batch job, not a request-path computation.
2. Label forward returns at 1h/4h/24h horizons; drop neutral band.
3. Walk-forward train (XGBoost/LightGBM per the `signal-classification` skill),
   embargo = horizon.
4. **Gate on beating the naive baseline out-of-sample.** If it does not, ship
   the regime classifier and the anomaly detectors and say so.

**Do not ship a "predictor" that has not cleared step 4.** A confident number
with no out-of-sample track record is the exact failure mode the editorial
standards forbid ("no price predictions without methodology disclosure").

---

## Summary

| Capability | Before | After this lane |
|---|---|---|
| Fact-check gate | Broken import, dead content engine, gate never called | Wired into the draft path, flag default ON, 5 wiring tests |
| TA substrate | Complete (93 tests) but unreachable | `GET /api/v1/ta/signals` + `/ta/stats`, 5 route tests |
| Predictor | Nothing | Honest assessment: regime + anomaly yes, price predictor no (no labels, no harness) |
