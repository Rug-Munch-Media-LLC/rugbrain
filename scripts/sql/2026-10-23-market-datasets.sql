-- 2026-10-23 — market / hostile-actor datasets for Rugbrain token context.
-- Database: wallet_memory (only DB the rmi user may create tables in).
-- Every table carries its source licence in the table COMMENT. Anything that is not
-- oss_ok is tagged internal_only: usable for our analysis, never redistributed.

CREATE TABLE IF NOT EXISTS wallet_memory.dataset_licences
(
    dataset          LowCardinality(String),
    table_name       String,
    licence          String,
    licence_status   LowCardinality(String),   -- oss_ok | internal_only | blocked
    redistribution   String,
    source_url       String,
    rows             UInt64,
    period_start     Nullable(Date),
    period_end       Nullable(Date),
    ingested_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (dataset, table_name)
COMMENT 'Provenance ledger: one row per ingested dataset/table pair. licence_status is the contract - internal_only rows must never be redistributed.';

-- (1) pump.fun launches + price trajectories. Source: Kaggle danmina/solana-pump-fun-launches-and-price-trajectories
CREATE TABLE IF NOT EXISTS wallet_memory.pumpfun_launches
(
    mint               String,
    seen_at            DateTime64(6, 'UTC'),
    creator            String,
    signature          String,
    name               String,
    symbol             LowCardinality(String),
    pool               String,
    uri                String,
    bonding_curve      String,
    initial_buy_sol    Nullable(Float64),
    initial_buy_tokens Nullable(Float64),
    creator_pct        Nullable(Float64),
    v_sol_curve        Nullable(Float64),
    v_tokens_curve     Nullable(Float64),
    market_cap_sol     Nullable(Float64),
    is_mayhem          Nullable(UInt8),
    creator_launches   Nullable(UInt32),
    rugcheck_score     Nullable(Float64),
    rugcheck_score_raw String,
    total_holders      Nullable(UInt32),
    insiders_detected  Nullable(UInt8),
    creator_balance    Nullable(Float64),
    top20_pct          Nullable(Float64),
    rugged             Nullable(UInt8),
    risks_json         String,
    ingested_at        DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(seen_at)
ORDER BY (mint, seen_at)
COMMENT 'LICENCE: CC0-1.0 (oss_ok). Source: kaggle danmina/solana-pump-fun-launches-and-price-trajectories, period 2026-09-08..2026-10-22. Join key: mint.';

CREATE TABLE IF NOT EXISTS wallet_memory.pumpfun_observations
(
    mint             String,
    symbol           LowCardinality(String),
    launched_at      DateTime64(6, 'UTC'),
    observed_at      DateTime64(6, 'UTC'),
    price_launch     Nullable(Float64),
    price_now        Nullable(Float64),
    price_change_pct Nullable(Float64),
    p25              Nullable(Float64),
    p50              Nullable(Float64),
    p75              Nullable(Float64),
    sol25            Nullable(Float64),
    sol50            Nullable(Float64),
    sol75            Nullable(Float64),
    slope_pct        Nullable(Float64),
    from_peak_pct    Nullable(Float64),
    speed_pct        Nullable(Float64),
    v_sol            Nullable(Float64),
    v_tokens         Nullable(Float64),
    real_sol         Nullable(Float64),
    real_tokens      Nullable(Float64),
    graduated        Nullable(UInt8),
    error            String,
    ingested_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(launched_at)
ORDER BY (mint, observed_at)
COMMENT 'LICENCE: CC0-1.0 (oss_ok). Source: kaggle danmina, 4-point intra-window price path (25/50/75/100%). Join: mint + launched_at.';

CREATE TABLE IF NOT EXISTS wallet_memory.pumpfun_ai_verdicts
(
    mint             String,
    symbol           LowCardinality(String),
    decided_at       DateTime64(6, 'UTC'),
    model            LowCardinality(String),
    decision         LowCardinality(String),
    prompt_data      String,
    exit_reason      String,
    exit_after_s     Nullable(Float64),
    change_after_pct Nullable(Float64),
    peak_pct         Nullable(Float64),
    trough_pct       Nullable(Float64),
    flash_result     String,
    flash_reason     String,
    ingested_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(decided_at)
ORDER BY (mint, decided_at)
COMMENT 'LICENCE: CC0-1.0 (oss_ok). Source: kaggle danmina ai_verdicts.csv. LLM verdict + realised outcome per mint.';

CREATE TABLE IF NOT EXISTS wallet_memory.pumpfun_migrations
(
    mint        String,
    migrated_at DateTime64(6, 'UTC'),
    pool        String,
    p0          Nullable(Float64), sol0   Nullable(Float64),
    p30         Nullable(Float64), sol30  Nullable(Float64),
    p60         Nullable(Float64), sol60  Nullable(Float64),
    p120        Nullable(Float64), sol120 Nullable(Float64),
    p300        Nullable(Float64), sol300 Nullable(Float64),
    chg30       Nullable(Float64), chg60  Nullable(Float64),
    chg120      Nullable(Float64), chg300 Nullable(Float64),
    peak_pct    Nullable(Float64),
    ingested_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (mint, migrated_at)
COMMENT 'LICENCE: CC0-1.0 (oss_ok). Source: kaggle danmina migrations.csv - PumpSwap graduations with post-migration price at 30/60/120/300s.';

-- (2) DefiLlama hacks
CREATE TABLE IF NOT EXISTS wallet_memory.hacks
(
    hack_date      Date,
    name           String,
    classification LowCardinality(String),
    technique      LowCardinality(String),
    amount         Nullable(Float64),
    chain          Array(String),
    bridge_hack    UInt8,
    target_type    LowCardinality(String),
    returned_funds Nullable(Float64),
    source         String,
    defillama_id   String,
    language       Nullable(String),
    ingested_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYear(hack_date)
ORDER BY (hack_date, name)
COMMENT 'LICENCE: none published -> internal_only. Source: api.llama.fi/hacks. Attribute + link, never redistribute.';

-- (3) OFAC SDN digital-currency addresses (subset of sdn.csv)
CREATE TABLE IF NOT EXISTS wallet_memory.sanctions_crypto
(
    address       String,
    address_lower String,
    chain         LowCardinality(String),
    uid           String,
    name          String,
    program       LowCardinality(String),
    sdn_type      LowCardinality(String),
    country       LowCardinality(String),
    source        LowCardinality(String),
    ingested_at   DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (address_lower, chain, uid)
COMMENT 'LICENCE: US Government work (oss_ok), extracted from sdn.csv Remarks. Sanctioned digital-currency addresses only - ground-truth hostile labels. One row per (address, entity): an address listed for two entities keeps both.';

-- (4) Binance public klines
CREATE TABLE IF NOT EXISTS wallet_memory.binance_klines
(
    symbol      LowCardinality(String),
    open_time   DateTime64(3, 'UTC'),
    open        Float64,
    high        Float64,
    low         Float64,
    close       Float64,
    volume      Float64,
    close_time  DateTime64(3, 'UTC'),
    quote_volume Float64,
    trades      UInt32,
    taker_base  Float64,
    taker_quote Float64,
    ignore_flag UInt8,
    ingested_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(open_time)
ORDER BY (symbol, open_time)
COMMENT 'LICENCE: none published -> internal_only. Source: data.binance.vision monthly spot klines (microsecond open_time in 2025+ files).';
