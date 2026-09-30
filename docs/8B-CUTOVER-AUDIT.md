# 8B cutover audit — 2026-09-30

Every `_8b` collection audited against its 2560 base. Verdict: **the cutover is
data-complete. No re-embed is required, and the 2560 generations are dominated —
retire them, do not migrate them.**

| | count |
|---|---|
| twins AHEAD of their base | 8 |
| twins EQUAL to their base | 27 |
| twins BEHIND their base | 1 |

Where the twins already exceed their base:

```
news_v2     161,050 -> 173,843  (+12,793)
wallet_v2     9,657 ->  10,278  ( +621  = the KOL wallets loaded 2026-09-30)
```

Everything else is equal, so every twin is a faithful superset of its base. All
twins are 4096-dim; verified individually, because qdrant reports the unnamed
dense vector as a flat `{size, distance}` dict and a naive probe mis-reads the
large collections as missing.

## The one behind

`scan_intel_8b` is 60,416 vs 60,483 base — behind by 67. That is **design, not
drift**: the payload has no text field, so there is nothing to re-embed. It is
address-keyed lookup data and it lives in ClickHouse, not as a semantic corpus.

## The real defect this audit found

`reembed-8b-tail.service` is named 8B, its Description claims the ContextCrypto
8B tail drain, and its retention comment says it EXCLUDED the `_8b` collections.
All 17 of its `--collections` targets were the **retired 2560 lanes**. A job whose
entire purpose is the 8B tail was re-embedding data nothing queries, on the
provider, continuously.

Retargeted to the twins:

```
security_patterns_8b  scam_intel_v2_8b  known_scams_8b  scam_patterns_8b
defi_exploits_8b  crypto_vulnerabilities_8b  exploits_8b  contract_security_8b
live_threats_8b  threat_intel_v2_8b  news_v2_8b  news_articles_8b
social_intel_8b  wallet_profiles_8b  wallet_v2_8b  regulation_enforcement_8b
rag_documents_8b
```

`ingest_wallet_labels.py` now targets `wallet_v2_8b`.

## Scale, for the people deleting things

```
codebase_reference_v2_8b   2,185,851
rag_documents_8b             536,079
news_v2_8b                  173,843
scam_intel_v2_8b            107,878
wallet_profiles_8b           40,355
```

## Before dropping any 2560 base

1. Nothing should still write to it — grep for the bare name, and check
   `scripts/*` `COLLECTION` constants, not just systemd units.
2. `dim_policy.py` must stop permitting a 2560 collection to be created, or the
   next run rebuilds the retired lane.
3. Take a qdrant snapshot first. The base is a superset target, not a backup.
