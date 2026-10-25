# CORPUS-SECURITY-NEXT — measured coverage vs. additive security sources

Date: 2026-10-25 · measured on Talos (Qdrant REST :6334) · method: full payload scan of every point
(no sampling — 11 collections, 86k points), `mech` = doc carries a mechanism (code_block > 40 chars,
fenced code, `pragma/contract/function` + >600 chars, or >600 chars of prose), not just a title/label.

## 1. Coverage per topic (n docs / % carrying a mechanism)

| topic | defi_exploits_8b 16.7k | security_patterns_8b 14.3k | github_audits_8b 839 | exploits_8b 1.3k | contract_audits_8b 1.05k | crypto_vulns_8b 5k | verdict |
|---|---|---|---|---|---|---|---|
| reentrancy | 416 / 75% | 1116 / 99% | 103 / 98% | 35 (labels) | 26 / 35% | 168 / 64% | covered (mechanism) |
| oracle manipulation | 1198 / 79% | 1226 / 96% | 63 / 97% | 89 (labels) | 11 / 100% | 82 / 45% | covered |
| access control | 544 / 50% | 1242 / 98% | 59 / 100% | 44 (labels) | 101 / **2%** | 375 / 36% | covered by patterns; audit side is titles-only |
| signatures / replay | 673 / 90% | 1044 / 98% | 80 / 96% | 55 (labels) | 22 / 91% | 71 / 72% | covered |
| proxies / upgrades | 705 / 94% | 1586 / 98% | 54 / 100% | 69 (labels) | 10 / 80% | 114 / 66% | covered |
| ERC-4626 / vault inflation | 64 / 94% | 362 / 99% | 14 / 100% | 10 | 13 / 92% | **0** | mechanism ok, real incidents thin |
| bridges / cross-chain | 715 / 71% | 948 / 98% | 76 / 97% | 56 | 28 / 96% | 23 / 87% | covered (+ bridge_architecture 1888 docs) |
| MEV | 156 / 84% | 554 / 100% | 39 / 97% | 14 | 22 / 23% | 28 / 75% | covered |
| Solana-specific | 2006 / **4%** | 1806 / 98% | 40 / 100% | 148 | 1 | 12 / 83% | **GAP: 2k Solana rows are rug-pull token labels, not program-level mechanisms** |
| incident post-mortems | 1305 / 60%* | 1074 / 97% | 68 / 97% | 113 | **0** | 3 / 33% | **GAP: *those are PoC code + date/amount metadata, not narrative root-cause prose. Zero post-mortems in audit_reports_8b (1.2k), contract_audits_8b, bridge_architecture** |

Structural findings:
- `exploits_8b`: **1,344 / 1,344 points have empty `content`** — labels-only confirmed, and `metadata`/`provenance`
  are stringified dicts. Repair job, not a new source.
- `known_scams_8b`: 42.6k points, 36% empty content, 11k label-only rows (fraud addresses / phishing domains).
  Not exploit mechanism; do not count it as security coverage.
- `security_patterns_8b` is the mechanism backbone: 96–99% mech on all ten topics.
- `defi_exploits_8b` is 8,330 chunks of `source=defihacklabs` PoC (Apache-2.0) — strong on EVM exploit code.
- `hacks_v2_8b` is effectively dead (13 points, 38% empty) — retire or refill.

## 2. Six candidate sources (licence first)

| # | source | licence (evidence) | volume | what it adds | fetch path | verdict |
|---|---|---|---|---|---|---|
| 1 | Immunefi disclosure archive | proprietary site ToS, no SPDX; bulk reuse needs written permission | ~2k disclosures | incident prose | immunefi.com/bug-bounty | **REJECT** (unlicensed; PoCs withheld) |
| 2 | Rekt.news write-ups | no licence — all rights reserved, attribution requested | ~450 posts | narrative post-mortems | rekt.news | **REJECT** (unlicensed) |
| 3 | Trail of Bits `publications` | **CC-BY-SA-4.0** (LICENSE file read: "Attribution-ShareAlike 4.0 International"; repo_facts agrees) | 966 files: 646 pdf, 145 md, 47 sol + `datasets/smart_contract_audit_findings` (247-row findings CSV) | audit-finding prose, root-cause narrative, per-finding taxonomy (type/severity/difficulty/static/dynamic/ERC20) | github.com/trailofbits/publications | **ACCEPT #1** (internal-only: ShareAlike forbids re-licensing; attribution required) |
| 3b | Spearbit `portfolio` | `license: null` (repo_facts) | ~150 reports | — | github.com/spearbit/portfolio | **REJECT** (unlicensed) |
| 3c | Code4rena reports | per-contest repos, no SPDX anywhere | ~400 contests | — | github.com/code-423n4/* | **REJECT** (per-report licence ambiguity) |
| 4 | Solodit-style aggregator (Cyfrin) | proprietary SaaS; ToS forbids scraping/redistribution | n/a | aggregated audit findings | solodit.xyz | **REJECT** (proprietary) |
| 5 | SEAL / DeFiHackLabs PoC | DeFiHackLabs **Apache-2.0** but **already held** (8,330 chunks in defi_exploits_8b); SEAL write-up repos return `license: null` | duplicative | — | github.com/SunWeb3Sec/DeFiHackLabs | **REJECT as new ingest** (duplicate); keep for Solana re-chunk |
| 6 | arXiv abstracts | arXiv API metadata reuse permitted; per-paper licence varies (CC-BY or arXiv-exclusive) — abstracts only | large, low density | formal mechanism analysis (MEV, oracle, bridges) | export.arxiv.org/api/query | **DEFER** (abstracts only, shallow vs. our 14k mechanism patterns) |
| + | Apache-2.0 ground-truth pack: `crytic/not-so-smart-contracts` (47 files: 25 sol, 20 md) + `smartbugs/smartbugs-curated` (158 files: 143 sol, SWC-tagged) | **Apache-2.0** both (LICENSE read) | 205 files | canonical SWC-labelled vulnerable/fixed pairs + minimal repro; gives an eval holdout we do not have | github.com/crytic/not-so-smart-contracts, github.com/smartbugs/smartbugs-curated | **ACCEPT #2** (small, fully licensed, additive) |

## 3. Top 2 — ingest plan (bounded; nothing ingested this pass)

### A. Trail of Bits publications — CC-BY-SA-4.0
- Scope: `papers/`, `reviews/`, `case-studies/`, `datasets/smart_contract_audit_findings/`; skip png/pptx/pdf-images.
- Extract: `pdftotext -layout` for PDFs, md as-is; chunk 1,200 tokens / 120 overlap; keep page range.
- Bound: pass 1 ≤ 400 docs / ≤ 6k chunks, run behind the 20-doc licence+extraction dry run.
- Collection: `audit_reports_8b` (or new `tob_publications_8b` if we want clean provenance split).
- Provenance: `source=trailofbits_publications`, `licence=cc-by-sa-4.0`, `source_url`, `doc_path`, `commit`,
  `page_range`, `sha256`, `extracted_at`, `ship_policy=internal-only-no-redistribution`.
- Dedupe: `sha256(normalized_text)` primary; `(source, doc_path, commit)` secondary; near-dup on first-200-token hash.
- Attribution: carry the upstream LICENSE + "© Trail of Bits, CC BY-SA 4.0" in the collection metadata.

### B. Apache-2.0 ground-truth pack — crytic + smartbugs
- Scope: whole repos, 205 files (143 .sol, 30 .md).
- Collection: `security_patterns_8b` with `verdict=weakness` + a held-out eval slice (do **not** train/tune on it).
- Provenance: `source=crytic_nssc|smartbugs_curated`, `licence=apache-2.0`, `path`, `commit`, `swc_id`
  (from `dataset/<class>/` and SWC refs in README), `sha256`.
- Dedupe: `sha256(file)`; then `(contract_name, sha256(src without comments))`.

## 4. Rejections and what stays uncovered
- Rejected: Immunefi (no licence), Rekt (no licence), Spearbit (license null), Code4rena (no SPDX),
  Solodit/Cyfrin (proprietary), SEAL write-ups (license null), DeFiVulnLabs (license null),
  DeFiHackLabs (already ingested, 8,330 chunks — not additive).
- Uncovered after this pass: (a) **Solana program-level mechanism** — only label rows; route = re-chunk the
  Apache-2.0 DeFiHackLabs Solana PoCs with explanation, or obtain written permission (Immunefi/SEAL);
  (b) **narrative incident post-mortems** — ToB covers audit-side only, the incident-side sources are all unlicensed;
  (c) `exploits_8b` repair (1,344 empty-content points, stringified provenance) — internal, no source needed.

## 5. Licence smoke test (this pass, 20-doc scale)
3 repos cloned `--depth 1 --filter=blob:none` on Talos (no blobs fetched beyond LICENSE):
ToB `LICENSE` = Attribution-ShareAlike 4.0 International · crytic = Apache-2.0 · smartbugs = Apache-2.0.
Sample listing inspected for 20 files per repo; no vectors written, no ingestion performed.
