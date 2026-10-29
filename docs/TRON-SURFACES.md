# Tron launchpad & core surfaces — registry + proofs (2026-10-25)

Worktree `rb-tron41` · base `forgejo/main` @ `1e589fc` · **no commit, no push**.
Every row was fetched live this session (TronGrid-less: no API keys, public pages only).

## 1. Surfaces

| id | project | where | licence (SPDX / class) | ref | pages | mechanics documented? |
|---|---|---|---|---|---|---|
| tron-devdocs | TRON core dev docs (ReadMe) | developers.tron.network (sitemap 381 URLs) | **no licence assertion** → `proprietary-public-docs` | live, 2026-10-25 | 381 discovered, cap 140 | API + resource model yes; **no launchpad content** |
| tron-devdocs-llms | same | `https://developers.tron.network/llms.txt` | same | live | 1 | agent index of the above |
| java-tron | `tronprotocol/java-tron` | github | **LGPL-3.0** ✅ API-verified | `b33eed89a6a4` (2026-09-11) | `repo_docs` (not page-crawled) | Yes — consensus/TVM/actuator source; TRC-10 in `Program`/`Repository`, energy in `EnergyProcessor`/`VMActuator` |
| sunio-docs | SUN.io (docs.sun.io, Docusaurus) | docs.sun.io sitemap **210 URLs** | no licence assertion → `proprietary-public-docs` | live | 210 discovered, cap 120 | Yes for SunPump concepts; **no curve formula** |
| sunpump-* (8 docs) | SunPump launchpad | docs.sun.io/protocols/sunpump/* (11 sitemap URLs) | `proprietary-public-docs` | live | 8 pinned + 3 sitemap-only | launch, fees, token details, contract+function reference listed; **no ABI bodies** |
| sunswap-contract | SunSwap V2/V3/V4 | github org names `sun-protocol/*` **404** | **unverified** | — | 0 | [REVIEW] no public repo found at guessed paths; treat as **closed-source gap** |
| sunpump-contract | SunPump factory | not published | **closed-source** | deployed `TTfvyrAz86hbZk5iDpKD78pqLGgi8C7AAw` (verified in our `lite-chain-indexer`) | 0 | only via our reverse-engineered selectors + docs prose |
| TRC-20 spec (TIP-20) | `tronprotocol/tip` / `tron-tip` | github guesses **404** | unverified | — | 0 | [REVIEW] canonical TIP repo path unconfirmed this session |

## 2. What was ingested / staged

- `config/bots_launchpads_manifest.json` extended with **11 entries** (`tron-devdocs`, `tron-devdocs-llms`, `sunio-docs`, 8× `sunpump-*`). Manifest now 35 entries.
- Pipeline: existing `bots_launchpads` source (`scripts/ingest_bots_launchpads.py` → `research_v2`_8b, 4096-d). Provenance travels per chunk: publisher, category, doc_type, `license`, `retrieved_on`, `canonical_id`.
- **Verified dry-run:** `--dry-run --category launchpad` discovers **170 docs** (170 launchpad_docs, 0 failures). Write pass NOT run — requires the 8B embed lane and writes to the live corpus.
- java-tron is `repo_docs` provenance, not page-crawled: clone at `b33eed89a6a4`, licence LGPL-3.0, publishable with attribution (copyleft).

## 3. Four proofs

**P1 — SunPump launch + graduation mechanics** (docs.sun.io, service-fees + how-to-launch):
launch costs **~20 TRX** creation fee; trading fee **1 %**; when market cap hits **100 % of the bonding curve** the contract **auto-seeds SunSwap V2** with **~100,000 TRX + the remaining 200,000,000 tokens** and **deducts ~3,000 TRX** from the curve as the liquidity-add fee. Launch form allows a **pre-purchase in the same transaction** (anti-snipe). Curve *shape* is not published → GAP.

**P2 — TRC-20 vs ERC-20**: docs state TRC-20 is *"fully compatible with ERC-20"* — the divergence is **runtime, not ABI**. Energy has **no free quota** (network limit 180,000,000,000) while Bandwidth has a **600 free** allowance (`getFreeNetLimit`, limit 43,200,000,000); `fee_limit` caps the caller-side energy budget *including* the TRX-burn remainder. java-tron: TRC-10 uses a `long tokenId` validated by `Program.checkTokenId`, and `callToAddress` takes an `isTokenTransfer` path that **refunds energy** when the token balance is short — i.e. TRC-10 value rides the TVM call, which is what `trcToken`/`tokenId` exposes to contracts. `[REVIEW]` exact Solidity global spelling not confirmed from source this session.

**P3 — Tron token holder/transfer data**: our verified path is TronGrid event polling normalized into EVM-style receipts (`lite_indexer/strategies/tron/launch.py`: `logs[]` with `address`/`topics`/`data`, TronGrid → `TokenCreate` match on `TTfvyr…` with topic0 `1ff0a01c…`). `[REVIEW]` the TronGrid doc slugs 404'd (`/docs/tron-grid*`), so endpoint-level citation is pending; the ingestion contract in our code is the evidence.

**P4 — launchpad fee in code**: fee **values** are doc-only (20 TRX, 1 %, ~3,000 TRX). Fee **math** exists in no public repo — the SunPump factory is closed. What we do hold in code: contract address, `TokenCreate` topic0, `launchToDEX(address)` selector (graduation trigger), and `buy/firstBuy/sell` selectors (`1cc2c911`/`2f70d762`/`d19aa2b9`) in `lite-chain-indexer`.

## 4. Gaps

1. **SunPump factory is closed-source** — no bonding-curve formula, no fee-split constants, no graduation threshold arithmetic. Only prose + our selectors.
2. **SunSwap contract repos not found** at `sun-protocol/*` — Tron DEX source unverified as OSS.
3. **TIP-20 / TRC-20 canonical spec** repo path unconfirmed (guessed repos 404) — spec citation must come from java-tron or the TIP site directly.
4. **TronGrid docs slugs 404** — need the llms.txt-driven crawl to map real endpoint paths (`/v1/accounts/{addr}/transactions/trc20`, contract events).
5. **Competing Tron memepads** not enumerated this session (only SunPump). `[REVIEW]`
6. All SUN/TRON docs pages are **proprietary-public-docs** → internal retrieval only, **not publishable**; only java-tron (LGPL-3.0) is redistributable.
