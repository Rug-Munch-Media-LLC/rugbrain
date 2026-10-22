# Market / hostile-actor datasets for Rugbrain — 2026-10-23

Storage: ClickHouse `wallet_memory` (rmi user may CREATE there; DROP/TRUNCATE/OPTIMIZE
need the `default` user). DDL: `scripts/sql/2026-10-23-market-datasets.sql`.
Ingest: `scripts/ingest_pumpfun_kaggle.py`, `ingest_hacks_ch.py`, `ingest_sanctions_ch.py`,
`ingest_binance_klines.py`. Read path: `src/rugbrain/token_context.py:token_context(mint)`.
Provenance: `wallet_memory.dataset_licences` (one row per dataset/table; read with `FINAL`).

## Tables, rows, licences (verified counts)

| table | rows | engine / order key | source | licence | status |
|---|---|---|---|---|---|
| pumpfun_launches | 1,069,889 | ReplacingMergeTree, (mint, seen_at) | Kaggle danmina (CC0) | CC0-1.0 | oss_ok |
| pumpfun_observations | 896,794 | ReplacingMergeTree, (mint, observed_at) | same | CC0-1.0 | oss_ok |
| pumpfun_ai_verdicts | 35,801 | ReplacingMergeTree, (mint, decided_at) | same | CC0-1.0 | oss_ok |
| pumpfun_migrations | 204 | ReplacingMergeTree, (mint, migrated_at) | same | CC0-1.0 | oss_ok |
| hacks | 1,311 | ReplacingMergeTree, (hack_date, name) | api.llama.fi/hacks (1,314 received, 3 exact dupes) | none published | internal_only |
| sanctions_crypto | 500 addresses / 106 entities | ReplacingMergeTree, (address_lower, chain, uid) | treasury.gov sdn.csv | US Gov work | oss_ok |
| binance_klines | 86,400 (2 symbols × 43,200) | ReplacingMergeTree, (symbol, open_time) | data.binance.vision | none published | internal_only |

Every table COMMENT carries `LICENCE: ...`; the two `internal_only` tables say
"do not redistribute" in both the table comment and the ledger row.

## Real schemas (read from the files, not assumed)

`launches.csv` (26 cols): mint, seen_at, creator, signature, name, symbol, pool, uri,
bonding_curve, initial_buy_sol, initial_buy_tokens, creator_pct, v_sol_curve,
v_tokens_curve, market_cap_sol, is_mayhem, creator_launches, rugcheck_score,
rugcheck_score_raw, total_holders, insiders_detected, creator_balance, top20_pct, rugged,
risks_json. Sparse — most enrichment columns are empty; loaded as NULL, never 0.

`observations.csv` (22 cols): mint, symbol, launched_at, observed_at, price_launch,
price_now, price_change_pct, p25, p50, p75, sol25, sol50, sol75, slope_pct, from_peak_pct,
speed_pct, v_sol, v_tokens, real_sol, real_tokens, graduated, error.
Join key: `mint` + `launched_at` (observations) / `seen_at` (launches).

Date ranges: launches 2026-09-08 13:11:05 → 2026-10-22 02:59:50 UTC;
observations 2026-09-09 07:30 → 2026-10-22 03:00 UTC.

`hacks`: 12 fields (date unix→Date, name, classification, technique, amount, chain[],
bridgeHack, targetType, source, returnedFunds, defillamaId, language). Span
2011-06-19 → 2026-10-21. Chain mix: Ethereum 543, BSC 262, Arbitrum 101, Base 65, Bitcoin 65.

`sdn.csv`: 12 columns, 19,472 rows. Crypto addresses live only in `Remarks` (col 12) in
the grammar `Digital Currency Address - <TICKER> <ADDRESS>` (repeats, `alt.` prefix).
Parser: `src/rugbrain/sanctions_addresses.py` (marker grammar, not a blind base58 regex —
avoids SWIFT/BIC false positives). Result: **106 entities → 500 unique addresses**
(TRX 87, XMR 6, USDT 41, BTC 254, ETH 87, LTC 8, ZEC 3, DASH 5, BTG 1, XVG 1, BCH 2,
SOL 2, DOGE 2, BNB 1).

`binance` monthly kline CSV: headerless, 12 columns —
open_time, open, high, low, close, volume, close_time, quote_volume, trades,
taker_buy_base, taker_buy_quote, ignore. **Timestamps are microseconds** in 2025+ files.
Proof load: BTCUSDT + SOLUSDT, 2026-09, 43,200 rows each (1-minute, full month).

Backfilling a 90-day window: three monthly calls per symbol (no need to touch `daily`):

```
for m in 2026-07 2026-08 2026-09; do
  python3 scripts/ingest_binance_klines.py --symbol SOLUSDT --month $m
done
```

Concurrency is per-symbol; the script downloads into
`/srv/data/raw/binance/` (cached, skipped when present) and streams rows straight into
`clickhouse-client` via docker exec, so memory stays flat.

## Query helper

`src/rugbrain/token_context.py::token_context(mint)` → dict with
`chain`, `anchor`, `launch`, `trajectory[]`, `verdict`, `nearby_hacks[]`, `sanctions[]`.
Chain is inferred from the mint (0x+40 hex → Ethereum, base58 32–44 → Solana).
Hack window: ±90 days around the launch date (or today), nearest 5 by absolute day
distance, filtered by chain membership.

### Lookup 1 — mint in the pump.fun window

`token_context("By2ZewrjUpeQARuuiXNHFrtiFUm2yq8REqeqrFeepump")` (symbol KNOB, Solana):
launch seen 2026-09-14 05:20:37, market_cap 32.11 SOL, creator_pct 6.68;
one trajectory row at 05:27:37 — price_change_pct **+1,305,292.7%**, from_peak_pct −4.2,
graduated 0; nearby Solana hacks: Dominion (2026-09-10, key compromise), Aquifer
(2026-08-31, $2.47M arbitrary external call), Avici (2026-08-28, $500.9K), Gamdom
(2026-10-13, $4.21M multi-chain), Coffer (2026-10-18, rounding error). sanctions: [].

### Lookup 2 — OFAC-sanctioned address

`token_context("6wjqWWra8ombzaw6VHrG5xpQ972jCYF6bbHiFCbWmr4U")`:
launch null, trajectory [] (not a pump.fun token), sanctions → **HIT**:
`SHPS SHELBIT`, program `SDGT`, chain SOL. Nearby Solana hacks still returned
(Coffer, Gamdom) so a sanctions hit is never presented without context.

## Caveats

- `hacks` stores 1,311 of 1,314 records: ReplacingMergeTree key (hack_date, name) collapses
  3 exact duplicates present in the upstream feed. Received count is logged by the ingest.
- `_chain_of()` labels any 32–44-char base58 string "Solana" — a BTC address queried as a
  mint would be mislabelled. [REVIEW] acceptable for Solana-first Rugbrain, worth a
  prefix-based disambiguation if EVM/BTC tokens enter the same path.
- The pre-existing Qdrant ingest `scripts/ingest_ofac_sanctions.py` still carries its own
  copy of the base58 regex list; `src/rugbrain/sanctions_addresses.py` is the parser used
  here. Consolidating them is a follow-up (that file was already dirty in the worktree).
- `rmi` ClickHouse user cannot DROP/TRUNCATE/OPTIMIZE; re-runs rely on the ReplacingMergeTree
  key plus `OPTIMIZE ... FINAL` as `default`. Truncating a table needs the `default` user.
