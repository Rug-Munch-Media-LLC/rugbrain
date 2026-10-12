# RugBrain Dataset Backlog

This document tracks researched datasets for ingestion into RugBrain's knowledge collections, targeting thin/empty collections identified in the coverage audit.

## Coverage Audit Results (Pre-Ingestion)
Based on Qdrant collection stats (127.0.0.1:6334):
- **education_8b**: 5,157 points (THIN - target for education content)
- **crypto_vulnerabilities_8b**: 4,918 points (THIN - target for vuln/scanner data)
- **scam_patterns**: 0 points (EMPTY - target for scam pattern data)
- **defi_exploits_8b**: 16,648 points (OK but could use more)
- **wallet_v2**: NOT FOUND or 0 points (EMPTY - target for wallet intel)
- **social_intel_8b**: 15 points (VERY THIN - target for social intelligence)
- **regulation_enforcement_8b**: 5,083 points (THIN - target for regulatory data)
- **contract_audits_8b**: 902 points (SKIP - just topped up per instructions)

## Researched Datasets

### 1. Crypto Education Corpus (HuggingFace)
- **Name**: Crypto Education Corpus (EN)
- **URL**: https://huggingface.co/datasets/kskada/crypto-education-en-corpus
- **Access Method**: HuggingFace datasets library (public, no auth required)
- **Size**: 3,487 documents (~3.6M words)
- **Schema**: markdown (text), title, url, source, word_count, depth, related_topics
- **Target Collection**: education_8b
- **License**: MIT (per dataset tags)
- **Status**: ✅ COMPLETED (ingested 24 chunks from 5 documents)
- **Verification**: Points increased from 5,157 → 5,181 (+24)

### 2. OFAC SDN Sanctions List
- **Name**: OFAC Specially Designated Nationals (SDN) List
- **URL**: https://www.treasury.gov/ofac/downloads/sdn.xml
- **Access Method**: Public XML/CSV/ZIP bulk downloads (no auth)
- **Size**: ~19,416 total entries (~2,000+ with crypto addresses)
- **Schema**: XML with uid, name, program, remarks (containing BTC/ETH/LTC/XRP/TRX/XMR/SOL addresses)
- **Target Collection**: crime_reports (feeds regulation_enforcement_8b/threat_intel_v2_8b indirectly)
- **License**: Public domain (US Government work)
- **Status**: ✅ COMPLETED (ingested 20 entries)
- **Verification**: Points increased from 0 → 20 in crime_reports

### 3. SlowMist Hack Incident Archive
- **Name**: SlowMist Hacked Blockchain Archive
- **URL**: https://hacked.slowmist.io/en/
- **Access Method**: Public web interface (scraping paginated list)
- **Size**: 2,307 hack events (~$39B losses)
- **Schema**: Target, loss amount, attack vector, chain/category, description, references
- **Target Collections**: defi_exploits_8b, exploits_8b, crypto_vulnerabilities_8b, threat_intel_v2_8b
- **License**: Publicly accessible for security research
- **Status**: ⏳ QUEUED (requires scraping implementation)
- **Verification**: Pending

### 4. Chainabuse API
- **Name**: Chainabuse Public API v1.2
- **URL**: https://docs.chainabuse.com/docs/welcome-to-chainabuse-api
- **Access Method**: Public API (requires Basic auth)
- **Size**: Largest multi-chain scam database (real-time)
- **Schema**: Reports with addresses, domains, categories (16 types), descriptions, losses, scammer contact info
- **Target Collections**: scam_patterns, known_scams_8b, social_intel_8b
- **License**: Public API with auth required
- **Status**: ⏳ QUEUED (requires auth handling)
- **Verification**: Pending

### 5. GoPlus Malicious Address API
- **Name**: GoPlus Malicious Address Database
- **URL**: https://gopluslabs.io/en/aml-api
- **Access Method**: Public API (free tier available)
- **Size**: 62,277 honeypot addresses + 52,876 other malicious addresses
- **Schema**: Addresses, chains, threat types, timestamps
- **Target Collections**: crypto_vulnerabilities_8b, scam_patterns, threat_intel_v2_8b
- **License**: Free tier available (paid for bulk)
- **Status**: ⏳ QUEUED (requires API integration)
- **Verification**: Pending

### 6. BlockSec Phalcon Security Incident List
- **Name**: BlockSec Security Incident List (Phalcon Explorer)
- **URL**: https://docs.blocksec.com/phalcon/security-incident-list
- **Access Method**: Public blog/posts with structured incident data
- **Size**: 146+ major attacks since Jan 2023 (>$1B losses)
- **Schema**: Incident records with root-cause analysis, PoC codes, loss amounts, chains
- **Target Collections**: defi_exploits_8b, exploits_8b, crypto_vulnerabilities_8b
- **License**: Publicly accessible information
- **Status**: ⏳ QUEUED (requires scraping/parsing)
- **Verification**: Pending

### 7. Phishing Blocklists (Multiple Sources)
- **Name**: Crypto-Phishing Domain Blocklists
- **URLs**: 
  - PhishTank: https://www.phishtank.com/
  - OpenPhish: https://openphish.com/
  - URLhaus: https://urlhaus.abuse.ch/browse/tag/crypto/
- **Access Method**: Public feeds (CSV, JSON, text)
- **Size**: Varies (1K-100K+ entries)
- **Schema**: Domains, URLs, IPs, threat types, timestamps
- **Target Collections**: social_intel_8b, scam_patterns
- **License**: Varies (typically free for security use)
- **Status**: ⏳ QUEUED (requires source selection and parsing)
- **Verification**: Pending

### 8. Academic Crypto Scam Datasets
- **Name**: University Research Datasets (e.g., IEEE papers, Kaggle)
- **URLs**: Various academic sources
- **Access Method**: Public downloads, API where available
- **Size**: Varies (100-10K+ entries)
- **Schema**: Typically includes labels, features, descriptions
- **Target Collections**: crypto_vulnerabilities_8b, scam_patterns
- **License**: Varies (check per dataset)
- **Status**: ⏳ QUEUED (requires source identification)
- **Verification**: Pending

## Implementation Summary

### Completed Ingestions
1. **OFAC Sanctions List** → crime_reports
   - Script: `scripts/ingest_ofac_sanctions.py`
   - Batch: 20 entries (--limit 20)
   - Result: +20 points in crime_reports (0 → 20)
   - Embedding: 4096-dim qwen3-embedding-8B (DeepInfra primary)

2. **Crypto Education Corpus** → education_8b
   - Script: `scripts/ingest_education.py`
   - Batch: 5 documents (--limit 5)
   - Result: +24 chunks in education_8b (5,157 → 5,181)
   - Embedding: 4096-dim qwen3-embedding-8B (DeepInfra primary)

### Next Steps (Queued)
- Implement remaining datasets in priority order:
  1. SlowMist Hack Archive (high impact for exploit data)
  2. Chainabuse API (high impact for scam patterns)
  3. GoPlus Malicious Addresses (broad threat intel coverage)
  4. BlockSec Phalcon Incident List (detailed exploit analysis)
  5. Phishing Blocklists (social intelligence)
  6. Academic datasets (supplemental validation)

## Notes
- All ingestions use deterministic UUIDv5 point IDs for idempotent re-runs
- Embeddings use canonical 8B lane (qwen3-embedding-8B) via DeepInfra primary
- Collections targeted match the 4096-dim embedding requirement
- Boundary conditions handled (empty content, rate limits, errors logged)
