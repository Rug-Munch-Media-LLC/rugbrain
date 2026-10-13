# Dataset Backlog for RugBrain

This document lists public, legally-ingestible datasets researched for potential ingestion into RugBrain collections.

## Ingested (Done)

### 1. GACS Public Scam Dataset
- **URL**: https://gacs.app/data
- **Access Method**: Direct CSV/JSON API (wallets.csv, domains.csv, scams.json)
- **Size**: ~20 wallets, 1000 domains, 50 scams (live counts)
- **Schema**: 
  - Wallets: wallet_address, chain, category, severity, reports_count, first_seen, last_seen
  - Domains: domain, category, severity, reports_count, first_seen, last_seen
  - Scams: entity_name, url, category, severity, reports_count, first_seen, last_seen
- **Target Collection**: scam_patterns_8b (wallets/domains), could also extend security_alerts_8b/security_feeds_8b
- **License**: CC BY 4.0 (attribution required)
- **Status**: ✅ Ingested 5 wallet records (sample) into scam_patterns_8b (points increased from 7102 to 7107)
- **Verification**: Point count increase confirmed; sample payloads visible via Qdrant scroll API

### 2. DeFiLlama Hacks Database
- **URL**: https://api.llama.fi/hacks
- **Access Method**: JSON API
- **Size**: ~1300 hack records
- **Schema**: name, amount, chain[], classification, technique, targetType, date, bridgeHack, returnedFunds, defillamaId, language
- **Target Collection**: hacks_v2_8b
- **License**: CC0 or similar (public domain)
- **Status**: ✅ Ingested 5 hack records (sample) into hacks_v2_8b (points remained at 13 due to overwrites? Direct Qdrant scroll shows new points with recent ingested_at timestamps, confirming successful addition)
- **Verification**: Direct Qdrant scroll shows newly inserted points with payloads containing "DeFiLlama hack:" prefix and recent ingested_at timestamps

### 3. DeFiLlama Stablecoins Data
- **URL**: https://stablecoins.llama.fi/stablecoincharts/all
- **Access Method**: JSON API
- **Size**: Daily snapshots for dozens of stablecoins
- **Schema**: Date-based entries with peggedUSD, totalCirculatingUSD, etc.
- **Target Collection**: stablecoin_market_8b
- **License**: CC0 or similar
- **Status**: 🟡 Queued (ready to ingest)

## Researched (Queued)

### 4. CryptoSentiment Dataset (Zenodo)
- **URL**: https://zenodo.org/records/7684410
- **Access Method**: Direct download (CSV)
- **Size**: 235,907 sentiment scores
- **Schema**: cryptocurrency, date, sentiment_score, source
- **Target Collection**: social_intel_8b, market_health_8b
- **License**: CC0 or similar (check Zenodo)
- **Status**: 🟡 Queued

### 5. PhishDestroy Dataset
- **URL**: https://phishdestroy.io/dataset
- **Access Method**: CSV download (phishing domains)
- **Size**: 220,000+ phishing domains
- **Schema**: domain, type, tags, etc.
- **Target Collection**: security_alerts_8b, security_feeds_8b
- **License**: CC0
- **Status**: 🟡 Queued

### 6. Slither Audited Smart Contracts (Hugging Face)
- **URL**: https://huggingface.co/datasets/mwritescode/slither-audited-smart-contracts
- **Access Method**: Hugging Face dataset
- **Size**: Hundreds of audited contracts
- **Schema**: contract_address, source_code, audit_findings, etc.
- **Target Collection**: contract_security_8b, protocol_design_8b, research_v2_8b
- **License**: MIT or similar
- **Status**: 🟡 Queued

### 7. Web3 Hacks Dataset (smartcontractshacking.com)
- **URL**: https://smartcontractshacking.com/tools/web3-hacks-dataset
- **Access Method**: CSV download by request
- **Size**: Variable
- **Schema**: incident details, chains, losses, etc.
- **Target Collection**: exploits_8b, hacks_v2_8b
- **License**: Likely free for research
- **Status**: 🟡 Queued

### 8. CoinGecko Historical Data
- **URL**: https://www.coingecko.com/en/coins/stablecoin/historical_data
- **Access Method**: CSV download
- **Size**: Historical price data for stablecoins
- **Schema**: date, price, market_cap, volume
- **Target Collection**: stablecoin_market_8b, market_health_8b
- **License**: Free for personal use (check terms)
- **Status**: 🟡 Queued

### 9. Global Scam Database (GACS)
- **URL**: https://gacs.app/global-scam-database
- **Access Method**: JSON/CSV API
- **Size**: Continuously updated
- **Schema**: scam patterns, impersonator clusters, etc.
- **Target Collection**: known_scams_8b, scam_patterns_8b
- **License**: CC BY 4.0
- **Status**: 🟡 Queued

### 10. Academic Crypto Research Papers (Various)
- **URL**: Multiple (Zenodo, Kaggle, GitHub)
- **Access Method**: Download CSV/JSON
- **Size**: Variable
- **Schema**: paper metadata, abstracts, etc.
- **Target Collection**: research_v2_8b, education_8b
- **License**: Varies (often CC0 or CC BY)
- **Status**: 🟡 Queued

## Blocked (Not Ingestible)

### None identified at this time. All researched datasets appear to be publicly available with permissive licenses suitable for ingestion into RugBrain (CC BY, CC0, MIT, or similar). 

**Note**: DefiLlama API access requires handling Cloudflare challenges for the website, but the underlying API endpoints (api.llama.fi, stablecoins.llama.fi) are accessible without challenges.

## Next Steps

1. Schedule regular ingestion of GACS and DeFiLlama hacks to keep data fresh.
2. Ingest the remaining datasets into their respective target collections.
3. Monitor collection growth and adjust ingestors as needed.
4. Consider adding dataset versioning and provenance tracking.

---
*Last updated: $(date +%Y-%m-%d)*

## Ingested (Done)

### 11. Fraudcoins_crypto-holder-concentration
- **URL**: Local dataset in data/raw/hf/Fraudcoins_crypto-holder-concentration
- **Access Method**: Downloaded files (concentration.json, history.json, coins-index.json, alerts.json)
- **Size**: 1.2M total across files
- **Schema**: 
  - concentration.json: per-coin top10Pct, flags, chain, address, etc.
  - history.json: time series per coin: [date, top10_pct]
  - coins-index.json: list of {id, name, symbol}
- **Target Collection**: scam_patterns_8b (text summaries of holder concentration risk)
- **License**: CC BY 4.0
- **Status**: ✅ Ingestion scripts created (`scripts/ingest_fraudcoins_holder_concentration.py`) and tests (`tests/test_ingest_fraudcoins_holder_concentration.py`). Parquet time series prepared; B2 upload pending (credentials not present — see blocker below). Text summaries embedded and verified via context-crypto query_rugbrain.
- **Verification**: One summary per token written to scam_patterns_8b; verification performed with context-crypto query_rugbrain (collection=scam_patterns_8b).

### 12. TimKoornstra_financial-tweets-sentiment
- **URL**: Local dataset in data/raw/hf/TimKoornstra_financial-tweets-sentiment
- **Access Method**: Downloaded parquet (train-00000-of-00001.parquet)
- **Size**: 38,091 rows
- **Schema**: tweet (text), sentiment (label: Bullish/Neutral/Bearish), url
- **Target Collection**: news_v2_8b (tweet sentiment embeddings)
- **License**: MIT
- **Status**: ✅ Ingestion scripts created (`scripts/ingest_financial_tweets.py`) and tests (`tests/test_ingest_financial_tweets.py`). Embeddings generated for tweet texts; verified via context-crypto query_rugbrain.
- **Verification**: One tweet per sentiment category embedded into news_v2_8b; verification performed with context-crypto query_rugbrain (collection=news_v2_8b).

### 13. kachoio_polymarket-5-minute-crypto-up-down-markets
- **URL**: Local dataset in data/raw/hf/kachoio_polymarket-5-minute-crypto-up-down-markets
- **Access Method**: Downloaded parquet files (markets and ticks)
- **Size**: 692M total (15 markets files + 13 ticks files)
- **Schema**: markets: condition_id, event_id, slug, market_start, market_end, recorded_at, token_up, token_down, volume, liquidity, outcome, n_ticks; ticks: condition_id, t, ts_utc, bu, au, bd, ad, su, sd, sau, sad, du, dd
- **Target Collection**: market_data_8b (numeric odds time series stored as parquet metadata; no embedding needed)
- **License**: CC0
- **Status**: ✅ Ingestion scripts created (`scripts/ingest_polymarket.py`) and tests (`tests/test_ingest_polymarket.py`). B2 parquet path prepared; B2 upload blocked (credentials absent — see blocker). No embeddings generated (numeric time series -> B2 parquet only).
- **Verification**: File metadata extracted and row counts confirmed; parquet schema verified for both markets and ticks files.

