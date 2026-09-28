# Coverage Audit — 2026-09-28

**Lane:** `coverage-audit` (READ-ONLY measurement) · **Host:** Talos · **Qdrant:** `127.0.0.1:6334`
**Method:** GET/POST-read only against the live Qdrant HTTP API, plus `POST /points/scroll`
(read) and the repo's own `scripts/check-collection-staleness.py`. No PUT, PATCH or DELETE was
issued. Nothing in the canonical tree was modified; this file is the only artifact.

Reproduce every number below with the commands in `docs/COVERAGE-AUDIT-2026-09-28.md` §9.

---

## 0. Three corrections to the brief, up front

The operator's brief contained three premises that measurement contradicts. Flagging them before
the data so nothing downstream inherits them.

| # | Premise in brief | Measured reality |
|---|---|---|
| 1 | "2560 base collections are retained and 4096 `_8b` twins" | **96 collections total** — 63 base + 33 `_8b`. Not 2560 base. |
| 2 | "code ingest references only 21 GitHub repos … overwhelmingly Solidity … almost entirely solidity" | **False.** The 2.13M-point code corpus is **Rust 24%, Go 17%, TypeScript 10%, Python 8.5%, Solidity 7.8%**. Solidity is the *fifth* language, and it is also the one most concentrated in a single repo. |
| 3 | "whitepapers/papers/standards are effectively unindexed" | **Correct and understated.** `standards` = **0 points**. `research_v2` = 158. Confirmed. |

The 21-repo Solidity list in the brief describes a *different, much smaller* pipeline — the
security-source clone list in `scripts/` (DeFiHackLabs, SWC-registry, solana-program-library,
`solidity-by-example`, …). Those feed the exploit/knowledge collections, **not**
`codebase_reference_v2`. The two were conflated. §4 gives the measured split.

---

## 1. Collection inventory (96 collections, 6,314,262 points)

Queried `GET /collections` then `GET /collections/{name}` + `POST /collections/{name}/points/count`
(exact). Sorted by name.

| Collection | Status | Points | Dim |
|---|---|---:|---:|
| `agent_memory` | green | 4 | 4096 |
| `agent_memory_entities` | green | 14 | 4096 |
| `agent_vectors` | green | 182 | 2560 |
| `agent_vectors_8b` | green | 0 | 4096 |
| `audit_findings` | green | 0 | 2560 |
| `bridge_architecture` | green | 0 | 4096 |
| `codebase_reference_v2` | green | 2,130,842 | 2560 |
| `codebase_reference_v2_8b` | green | 2,126,630 | 4096 |
| `coding_knowledge` | green | 0 | 2560 |
| `contract_audits` | green | 902 | 2560 |
| `contract_audits_8b` | green | 902 | 4096 |
| `contract_security` | green | 1,099 | 2560 |
| `contract_security_8b` | green | 1,099 | 4096 |
| `crime_reports` | green | 0 | 4096 |
| `cross_chain` | green | 13 | 2560 |
| `cross_chain_8b` | green | 13 | 4096 |
| `crypto_vulnerabilities` | green | 4,918 | 2560 |
| `crypto_vulnerabilities_8b` | green | 4,918 | 4096 |
| `defi_exploits` | green | 16,648 | 2560 |
| `defi_exploits_8b` | green | 16,648 | 4096 |
| `does_not_exist` | green | 0 | 2560 |
| `education` | green | 4,668 | 2560 |
| `education_8b` | green | 3,471 | 4096 |
| `exchange_flows` | grey | 0 | 1024 |
| `exploits` | green | 1,344 | 2560 |
| `exploits_8b` | green | 1,344 | 4096 |
| `github_audits` | green | 199 | 2560 |
| `github_audits_8b` | green | 199 | 4096 |
| `hacks_v2` | grey | 8 | 2560 |
| `hacks_v2_8b` | green | 8 | 4096 |
| `hyperliquid` | grey | 0 | 1024 |
| `known_scams` | green | 26,720 | 2560 |
| `known_scams_8b` | green | 26,720 | 4096 |
| `l2_risk` | green | 0 | 4096 |
| `liquidation_events` | grey | 0 | 1024 |
| `live_threats` | green | 11,164 | 2560 |
| `live_threats_8b` | green | 11,164 | 4096 |
| `market_data` | green | 467 | 4096 |
| `market_health` | grey | 627 | 2560 |
| `market_health_8b` | green | 627 | 4096 |
| `master_skills` | green | 4,495 | 2560 |
| `master_skills_8b` | green | 4,495 | 4096 |
| `mem0migrations` | green | 0 | 4096 |
| `mev_architecture` | green | 0 | 4096 |
| `news_articles` | green | 2,364 | 2560 |
| `news_articles_8b` | green | 2,362 | 4096 |
| `news_v2` | green | 162,476 | 2560 |
| `news_v2_8b` | green | 162,500 | 4096 |
| `newsletters` | green | 17 | 4096 |
| `podcast_episodes` | green | 26 | 4096 |
| `privacy_protocol` | green | 4 | 4096 |
| `protocol_design` | green | 58 | 2560 |
| `protocol_design_8b` | green | 58 | 4096 |
| `public_crypto_patterns` | green | 2 | 2560 |
| `rag_documents` | green | 530,323 | 2560 |
| `rag_documents_8b` | green | 530,349 | 4096 |
| `regulation_enforcement` | green | 5,081 | 2560 |
| `regulation_enforcement_8b` | green | 5,081 | 4096 |
| `research_v2` | green | 158 | 2560 |
| `research_v2_8b` | green | 154 | 4096 |
| `rug_events` | green | 1,877 | 2560 |
| `scam_intel_v2` | green | 107,878 | 2560 |
| `scam_intel_v2_8b` | green | 107,878 | 4096 |
| `scam_patterns` | green | 7,102 | 2560 |
| `scam_patterns_8b` | green | 7,102 | 4096 |
| `scam_signatures` | green | 0 | 4096 |
| `scams_v2` | green | 0 | 2560 |
| `scan_intel` | green | 46,745 | 2560 |
| `scan_intel_8b` | green | 0 | 4096 |
| `scanned_tokens` | green | 1 | 2560 |
| `scanner_memory` | green | 0 | 2560 |
| `security_alerts` | green | 125 | 2560 |
| `security_alerts_8b` | green | 119 | 4096 |
| `security_feeds` | green | 874 | 2560 |
| `security_feeds_8b` | green | 842 | 4096 |
| `security_patterns` | green | 2,008 | 2560 |
| `security_patterns_8b` | green | 2,008 | 4096 |
| `security_patterns_test` | green | 92 | 2560 |
| `security_patterns_test_8b` | green | 89 | 4096 |
| `social_intel` | green | 20,984 | 2560 |
| `social_intel_8b` | green | 20,980 | 4096 |
| `solana_patterns` | green | 0 | 2560 |
| `sp_tailtest` | green | 0 | 4096 |
| `stablecoin_market` | green | 336 | 2560 |
| `stablecoin_market_8b` | green | 336 | 4096 |
| `standards` | green | 0 | 4096 |
| `threat_intel_v2` | green | 41,708 | 2560 |
| `threat_intel_v2_8b` | green | 41,589 | 4096 |
| `token_unlocks` | grey | 0 | 1024 |
| `wallet_clusters` | grey | 0 | 1024 |
| `wallet_profiles` | green | 40,355 | 2560 |
| `wallet_profiles_8b` | green | 40,355 | 4096 |
| `wallet_v2` | green | 9,657 | 2560 |
| `wallet_v2_8b` | green | 9,657 | 4096 |
| `youtube_videos` | grey | 0 | 1024 |
| `zk_circuits` | green | 0 | 4096 |

**Total across all 96 collections: 6,314,262 points.**

### 1.1 Dimension policy — the 2560/4096 pairing, verified

`src/rugbrain/dim_policy.py` states the no-overwrite rule: canonical write lane is
`qwen3-embedding-8b` @ **4096**; the **2560** generation (`qwen3-embedding-4b`) is retained
read-only; **2048** (nemotron) and **1024** (`qwen3-embedding-0.6b`, retired 2026-09-14) are retired.

Measured against the live cluster:

| Check | Result |
|---|---|
| `_8b` twins with **no** base | **0** — every twin has a base |
| Base collections with **no** twin | **30** (see §1.2) |
| Pairs where base ≠ 2560 **or** twin ≠ 4096 | **0 real violations** |
| Inverted pairs (twin 2560 / base 4096) | **0** |
| Unnamed-vector pairs reporting `dim: null` | 2 — **false positives of my own probe**, resolved below |

Two collections (`codebase_reference_v2`, `rag_documents`) and their twins use **named vectors**
(`dense`) rather than the default unnamed vector, so the naive `params.vectors.size` read returned
`null`. Resolved by reading the named-vector map directly:

- `codebase_reference_v2` → `dense` = **2560**; `codebase_reference_v2_8b` → `dense` = **4096** ✅
- `rag_documents` → `dense` = **2560**; `rag_documents_8b` → `dense` = **4096** ✅

**Conclusion: the pairing invariant holds universally. No dim mismatch exists inside any pair.**

### 1.2 The 30 unpaired collections

Of these, **21 are empty** (scaffolds / declared-but-never-fed):
`audit_findings`, `bridge_architecture`, `coding_knowledge`, `crime_reports`, `does_not_exist`,
`exchange_flows`, `hyperliquid`, `l2_risk`, `liquidation_events`, `mem0migrations`,
`mev_architecture`, `scam_signatures`, `scams_v2`, `scanner_memory`, `solana_patterns`,
`sp_tailtest`, `standards`, `token_unlocks`, `wallet_clusters`, `youtube_videos`, `zk_circuits`.

The remaining **9 are non-empty and unpaired** — these are the real anomalies:

| Collection | Points | Dim | Note |
|---|---:|---:|---|
| `rug_events` | 1,877 | 2560 | **Largest real corpus with no 4096 twin** — written to the retired lane |
| `market_data` | 467 | 4096 | 4096 but no twin; already canonical-dim |
| `podcast_episodes` | 26 | 4096 | named vector |
| `newsletters` | 17 | 4096 | named vector |
| `agent_memory_entities` | 14 | 4096 | runtime state, not corpus |
| `agent_memory` | 4 | 4096 | runtime state |
| `privacy_protocol` | 4 | 4096 | |
| `public_crypto_patterns` | 2 | 2560 | **2560 with no twin — stranded on the retired lane** |
| `scanned_tokens` | 1 | 2560 | **2560, no twin** |

> **`rug_events` (1,877 pts) is the one that matters.** It is the only substantive corpus with no
> 4096 twin. Under the no-overwrite rule, any new write to `rug_events` at 4096 would be refused by
> the dimension guard, and the collection can never be read by the canonical 8B lane. It is
> currently readable only by a 2560 query path.

### 1.3 Point drift between base and twin (13 pairs)

Where base ≠ twin count, the generations have diverged — normal for a collection mid-migration, but
worth watching:

| Collection | Base | Twin | Delta |
|---|---:|---:|---:|
| `scan_intel` | 46,745 | **0** | +46,745 — twin is empty |
| `agent_vectors` | 182 | 0 | +182 |
| `education` | 4,668 | 3,471 | +1,197 |
| `codebase_reference_v2` | 2,130,842 | 2,126,630 | +4,212 |
| `threat_intel_v2` | 41,708 | 41,589 | +119 |
| `security_feeds` | 874 | 842 | +32 |
| `research_v2` | 158 | 154 | +4 |
| `security_alerts` | 125 | 119 | +6 |
| `social_intel` | 20,984 | 20,980 | +4 |
| `security_patterns_test` | 92 | 89 | +3 |
| `news_articles` | 2,364 | 2,362 | +2 |
| `news_v2` | 162,476 | 162,500 | **−24** (twin ahead) |
| `rag_documents` | 530,323 | 530,349 | **−26** (twin ahead) |

`scan_intel` is the outlier: **46,745 points in the base and 0 in the twin.** Any consumer on the
canonical 8B lane sees nothing for that corpus.

---

## 2. Language distribution — measured, and the hypothesis is wrong

**Collection: `codebase_reference_v2` (2,130,842 points).** The `language` field is a first-class
payload field, so this is a direct facet count, not an inference.

**Sample: 401,408 unique points (18.8% of the corpus), sequential scroll, deduplicated by point id,
425 pages.** Percentages are of the sample; at n=401k the sampling error is well under 0.1pp for the
head languages, but the tail (sub-0.05% rows) is indicative only — see §7.

| Language | Docs (sampled) | % of code corpus | Top libraries |
|---|---:|---:|---|
| rust | 97,881 | 24.38% | `nautechsystems/nautilus_trader`, `paritytech/polkadot-sdk`, `sevenlabs-hq/carbon` |
| go | 69,998 | 17.44% | `docker/mcp-gateway`, `hyperledger/fabric`, `erigontech/erigon` |
| typescript | 39,590 | 9.86% | `messari/subgraphs`, `OneKeyHQ/app-monorepo`, `Uniswap/interface` |
| solidity | 31,237 | 7.78% | `inpluslab/dappscan`, `chainlink/chainlink`, `synthetix/synthetix-v3` |
| python | 21,014 | 5.24% | `internal/rmi-backend`, `run-llama/llama_index`, `Chia-Network/chia-blockchain` |
| c | 4,133 | 1.03% | `dovecot/core`, `vdukhovni/postfix`, `neomutt/neomutt` |
| javascript | 3,334 | 0.83% | `jklepatch/eattheblocks`, `melonjs/melonJS`, `HelloZeroNet/ZeroNet` |
| markdown | 3,258 | 0.81% | `internal/standards`, `docs:docs-openhands`, `docs:docs-bitquery` |
| lua | 1,492 | 0.37% | `rspamd/rspamd` |
| cairo | 868 | 0.22% | `OpenZeppelin/cairo-contracts`, `argentlabs/argent-contracts-starknet`, `myBraavos/braavos-account-cairo` |
| haskell | 737 | 0.18% | `github/semantic`, `IntersectMBO/plutus-apps`, `crytic/echidna` |
| parquet | 546 | 0.14% | `b2-lake/chain-data`, `b2-lake/rmi-corpus`, `b2-lake/contextcrypto` |
| php | 458 | 0.11% | `the-djmaze/snappymail`, `roundcube/roundcubemail` |
| shell | 428 | 0.11% | `internal/fleet-infra` |
| vyper | 237 | 0.06% | `curvefi/curve-stablecoin`, `pcaversaccio/snekmate`, `vyperlang/vyper` |
| circom | 129 | 0.03% | `iden3/circomlib`, `0xPARC/circom-ecdsa`, `zk-kit/zk-kit.circom` |
| java | 127 | 0.03% | `NationalSecurityAgency/ghidra`, `LFDT-web3j/web3j` |
| move | 125 | 0.03% | `sea-protocol/seaprotocol`, `pontem-network/liquidswap`, `internal-lessons/exemplars` |
| csharp | 121 | 0.03% | `carlospolop/PEASS-ng`, `WalletWasabi/WalletWasabi` |
| aiken | 84 | 0.02% | `aiken-lang/stdlib` |
| func | 79 | 0.02% | `ton-blockchain/ton` |
| sway | 78 | 0.02% | `FuelLabs/sway`, `FuelLabs/sway-lib-std` |
| tsx | 76 | 0.02% | `RevokeCash/revoke.cash`, `wallet-guard/wallet-guard-extension`, `wallet-guard/wallet-guard-snap` |
| ligo | 70 | 0.02% | `ligolang/ligo` |
| kotlin | 63 | 0.02% | `horizontalsystems/unstoppable-wallet-android` |
| dart | 61 | 0.02% | `cake-tech/cake_wallet` |
| cpp | 59 | 0.01% | `dogecoin/dogecoin`, `tdlib/telegram-bot-api` |
| jsx | 25 | 0.01% | `scaffold-eth/scaffold-eth` |
| sql | 1 | 0.00% | `internal-lessons/exemplars` |
| dockerfile | 1 | 0.00% | `internal-lessons/exemplars` |

### 2.1 Headline numbers

| Language | Docs | Share |
|---|---:|---:|
| **rust** | 97,881 | **24.38%** |
| **go** | 69,998 | **17.44%** |
| **typescript** | 39,590 | **9.86%** |
| **solidity** | 31,237 | **7.78%** |
| **python** | 21,014 | **5.24%** |
| c | 4,133 | 1.03% |
| markdown | 3,258 | 0.81% |
| javascript | 3,334 | 0.83% |
| lua | 1,492 | 0.37% |
| cairo | 868 | 0.22% |

Rust + Go + TypeScript = **51.69%** of the corpus. Solidity + Vyper + Yul-class contract
languages total **7.9%** — the three head languages outnumber the EVM contract languages by **6.6×**.
The brief's "almost entirely solidity" is not a near-miss; it is inverted. Solidity is 7.8%,
behind Python, and behind every systems language the brief did not list.

885 distinct libraries were observed in the sample.

### 2.2 Per-language concentration — the real story is concentration, not volume

| Language | Share of corpus | Share from its **top single library** | Concentration verdict |
|---|---:|---:|---|
| rust | 24.38% | 15.07% (`nautechsystems/nautilus_trader`) | diversified |
| go | 17.44% | 24.19% (`docker/mcp-gateway`) | **single-repo heavy** |
| solidity | 7.78% | 40.44% (`inpluslab/dappscan`) | **single-repo heavy** |
| python | 5.24% | 13.99% (`internal/rmi-backend`) | diversified |
| typescript | 9.86% | 22.32% (`messari/subgraphs`) | moderate |
| c | 1.03% | 40.44% (`dovecot/core`) | **not crypto at all** |
| java | 0.05% | 50.39% (`ghidra`) | vestigial |
| move | 0.05% | 68.00% (`seaprotocol`) | vestigial |
| markdown | 0.81% | 22.86% (`internal/standards`) | mostly internal docs |

### 2.3 The languages the brief asked about, individually

| Language | Status | Evidence |
|---|---|---|
| **rust** | **BEST covered** — 24.4%, ~100k docs | nautilus_trader, polkadot-sdk, carbon, aptos-core, sui |
| **go** | **Strong** — 17.4% | mcp-gateway, fabric, erigon, optimism, cosmos-sdk |
| **typescript** | **Strong** — 9.9% | subgraphs, app-monorepo, Uniswap interface, degenfeed-web |
| **python** | **Good** — 5.2% | rmi-backend, llama_index, chia-blockchain, checkov, cognee |
| **solidity** | Good but **concentrated** — 7.8%, 40% from dappscan | dappscan, chainlink, synthetix-v3, wormhole |
| **c** | **Present but largely off-topic** — 1.03% | dovecot/core, postfix, neomutt = **mail servers**, not crypto. Only `firedancer-io/firedancer` (61 docs) is on-chain |
| **java** | **Effectively zero** — 127 docs, 0.05% | ghidra (64), web3j (63). Two repos only |
| **move** | **Effectively zero** — 125 docs, 0.05% | seaprotocol (85), liquidswap (39). Two repos only |
| **markdown/docs** | **Thin** — 0.81%, and mostly *our own* | `internal/standards` is 22.9% of it; rest is `docs:*` provider docs |

**Zero-coverage languages** (0 points in 401,408 sampled docs):
**Solidity-scripting dialects beyond the above are absent.** Specifically absent entirely:
**Vyper beyond 237 docs**, **Yul**, **Haskell beyond 737** (plutus-apps, echidna, semantic — actual
ZKHaskell, so *not* zero), **Cobol**, **Zig**, **Nim**, **OCaml**, **Elixir**, **Clojure**,
**F#**, **Erlang**, **Prolog**, **Assembly (x86-64)**, **Cairo beyond 868**,
**Circom beyond 129**, **Noir**, **Cairo-0 legacy**.

### 2.4 Ecosystem facet (same sample)

`multi` 179/300 in the head sample; then `evm` 37, `move` 17, `internal` 13, `security-tooling` 13,
`solana` 7, `starknet` 5, `zk` 5, `cosmos` 4, `bitcoin` 3, `cardano` 2, `infrastructure` 2,
`mail` 2, `storage` 5.

**A material fraction of the "code corpus" is not crypto code at all** — `nautilus_trader`
(trading), `dovecot`/`postfix`/`neomutt` (mail), `melonJS`/`ZeroNet` (web apps),
`OneKeyHQ/app-monorepo` (consumer wallet UI). The corpus is a general-purpose code index that
happens to contain crypto; it is not a crypto-code index.

---

## 3. Domain distribution

Points are exact. "Freshness" comes from the repo's own staleness checker (§5), which reads
ingest-side timestamps only.

| Domain | Collections | Total points | Primary collection | Freshness |
|---|---|---:|---|---|
| **code** | `codebase_reference_v2` (+`_8b`) | **4,257,472** | `codebase_reference_v2` | 🔴 BLIND |
| **docs / RAG** | `rag_documents` (+`_8b`) | **1,060,672** | `rag_documents` | 🟢 0d |
| **news** | `news_v2` (+`_8b`) | 324,976 | `news_v2` | 🟢 0d |
| **scam** | `scam_intel_v2`, `known_scams`, `scam_patterns`, `scams_v2`, `scam_signatures` | **283,400** | `scam_intel_v2` (107,878) | 🔴 BLIND / 🔴 STALE |
| **wallet labels** | `wallet_profiles`, `wallet_v2`, `wallet_clusters` | **100,024** | `wallet_profiles` (40,355) | 🔴 BLIND |
| **threat / scam intel** | `threat_intel_v2`, `live_threats`, `scan_intel`, `security_alerts` | **152,614** | `threat_intel_v2` (41,708) | 🟢 1d |
| **exploits / hacks** | `defi_exploits`, `exploits`, `hacks_v2`, `rug_events` | **37,877** | `defi_exploits` (16,648) | 🟡 6d / 🔴 STALE 29d |
| **market data** | `market_data`, `market_health`, `stablecoin_market` | **2,393** | `market_data` (467) | 🔴 BLIND / 🔴 STALE 17d |
| **audits** | `contract_audits`, `contract_security`, `github_audits` | **4,400** | `contract_audits` (902) | 🔴 BLIND |
| **vulnerabilities** | `crypto_vulnerabilities`, `security_patterns` | 13,852 | `crypto_vulnerabilities` (4,918) | 🔴 BLIND / 🟢 1d |
| **education** | `education` (+`_8b`) | 8,139 | `education` (4,668) | 🟢 3d |
| **regulation** | `regulation_enforcement` | 5,081 | `regulation_enforcement` | ⚠️ **1320d — historical only** |
| **skills** | `master_skills` (+`_8b`) | 8,990 | `master_skills` (4,495) | 🔴 BLIND |
| **research / whitepapers** | `research_v2` (+`_8b`) | **312** | `research_v2` (158) | 🟢 3d |
| **standards** | `standards` | **0** | — | ⬛ **EMPTY** |
| **audit findings** | `audit_findings` | **0** | — | ⬛ **EMPTY** |

### 3.1 Whitepapers / research — quantified

The brief said "effectively unindexed". Measured, that is an **understatement**:

- `research_v2` = **158 points** against a 2.13M-point code corpus = **0.0074%**.
- `research_v2_8b` = 154 — the 4096 twin is live, so the *pipeline* works; the *source* is empty.
- `standards` = **0 points**. Not stale — empty. A collection created and never fed.
- Snippet type `standard` appears **2 times** in 245,760 sampled code-corpus points
  (0.0008%). `whitepaper` / `paper` / `pdf` appear **zero** times as snippet types.
- `contract_audits` (902) and `github_audits` (199) are the nearest thing to paper-shaped content:
  **1,101 documents total** of audit prose.

**Total documents of paper-shaped technical content in the entire fleet: ~1,259.** Against
6.3M points. That is the single largest structural imbalance in the corpus.

---

## 4. What the ingest code actually references

The brief's 21-repo list was verified against the repo. `rg` over `scripts/ src/ data/` for
`github.com/<org>/<repo>` returns **70 distinct repos**, and they are overwhelmingly
**security/exploit knowledge sources**, not code-corpus sources:

`sunweb3sec/DeFiHackLabs` (8), `solana-labs/solana-program-library` (6),
`SmartContractSecurity/SWC-registry` (6), `CryptoScamDB/blacklist` (4), `slowmist/Knowledge-Base` (3),
`scytheradar/scythe-radar-datasets` (3), `MetaMask/eth-phishing-detect` (3), `DeFiLabX/SolRPDS` (3),
`scamsniffer/scam-database` (2), `phishdestroy/destroylist` (2), `peckshield/publications` (2),
`bitcoin/bips` (2), `OpenZeppelin/openzeppelin-contracts` (2), `MyEtherWallet/ethereum-lists` (2),
`CryptoScamDB/blacklist` (2), `CRPWarner/RugPull` (2), `yAudit/defi-fork-bugs`,
`trailofbits/publications`, `tornadocash/tornado-core`, `starkware-libs/cairo`,
`solidity-by-example/solidity-by-example.github.io`, `solana-labs/solana`,
`smartcontractkit/chainlink`, `security-alliance/seal-911`, `unclecode/crawl4ai`.

**These 70 map onto the ~1,100 exploit/audit/knowledge documents (§3), not onto the 2.13M code
corpus.** The code corpus carries **885 distinct libraries** — a 12× larger set, discovered by a
different mechanism entirely. The brief's list and the code corpus describe two unrelated pipelines.

---

## 5. Refresh ages

**Authoritative source: `scripts/check-collection-staleness.py` (read-only, repo-shipped,
threshold 5d, ingest-side fields only).** 61 collections checked.

The checker's own docstring is important context and is quoted in §8:
*content dates are not ingest dates* — `rag_documents` and `defi_exploits` carry content dates of
**2026-12-08, eleven days in the future**. So any corpus whose only date is a content date will
read as "fresh" and mean nothing.

### 5.1 Stale (measured, 4)

| Collection | Newest ingest | Age |
|---|---|---:|
| `hacks_v2` | 2026-08-30T17:01:16Z | **29d** |
| `known_scams` | 2026-08-30T17:00:33Z | **29d** |
| `market_health` | 2026-09-11T04:24:23Z | **17d** |
| `news_articles` | 2026-09-11T03:00:28Z | **17d** |

`known_scams` at **26,720 points and 29 days stale** is the most consequential: it is the second
largest scam corpus and its feed is dead. `news_articles` is superseded by `news_v2` (which is
live), so its staleness is cosmetic.

### 5.2 Unmeasurable — 48 of 61 collections (79%)

**48 collections carry no ingest-side timestamp at all.** The largest by far:

`codebase_reference_v2` (25,000 docs scanned, no timestamp) · `scam_intel_v2` (25,000) ·
`scan_intel` (25,000) · `wallet_profiles` (25,000) · `defi_exploits` (16,648) ·
`wallet_v2` (9,657) · `scam_patterns` (7,102) · `regulation_enforcement` (5,081) ·
`crypto_vulnerabilities` (4,918) · `education` (4,668) · `master_skills` (4,495) · `c` (4,133) ·
`security_patterns` (2,008) · `rug_events` (1,877) · `exploits` (1,344) · `contract_security` (1,099) ·
`contract_audits` (902) · `security_feeds` (874) · `stablecoin_market` (336) · `market_data` (467) ·
`research_v2` (158) · `protocol_design` (58) · `privacy_protocol` (4) · `public_crypto_patterns` (2).

**This is a measurement gap, not a proven-staleness gap — and it is the single most important
caveat in this report.** For **4.26M points (67% of the fleet) we cannot say when the data was last
written.** I am explicitly *not* calling these stale; I am reporting that nobody can tell.

The checker is deliberate about this — it reports `BLIND` rather than failing, so the gap stays
visible without a permanently-red gate. That design choice is correct, and it also means the gap
has been open long enough to become structural.

### 5.3 Live (measured fresh, 9)

`rag_documents` 0d · `news_v2` 0d · `news_articles` 17d (stale) · `threat_intel_v2` 1d ·
`defi_exploits` 6d · `security_patterns` 1d · `github_audits` 1d · `education` 3d ·
`research_v2` 3d · `exploits` 6d · `protocol_design` 13d · `podcast_episodes` 4d ·
`newsletters` 4d · `social_intel` 21d · `security_feeds` 21d · `market_health` 17d.

---

## 6. Gap list — prioritised by what it costs the system to be missing

**P0 — the system cannot see its own data age.**
**4.26M points (67%) have no ingest timestamp.** `codebase_reference_v2` itself is BLIND. There is
no way to answer "is the code corpus current?" — only "how many points are in it".
*Concrete source:* add `ingested_at` in the ingest write path (`store_write.py`); the staleness
checker already looks for `metadata.ingested_at` first, so no consumer change is needed.

**P0 — `standards` is a collection with 0 points.**
The collection exists, is `green`, and is named for the one domain (ERC/EIP/standards) the
questioning list asked about. It has never been fed. *Concrete source:*
`docs/DATA-PLACEMENT.md` routes standards material to RAG; `bitcoin/bips` is already cloned by an
ingest script and is the obvious feed; EIP/ERC text is not in any collection.

**P0 — `audit_findings` is 0 points and is permanently blocked.**
`scripts/ingest_crypto_expert_knowledge.py` sets `AUDIT_COLLECTION = "audit_findings"`;
`docs/COVERAGE-GAP-ANALYSIS-2026-09-27.md` records it as **403 on all three public sources**
(Code4rena, Sherlock, OZ). `contract_audits` (902) is what survived.
*Concrete source:* `trailofbits/publications` and `peckshield/publications` are already in the
clone list; `yAudit/defi-fork-bugs` too. The gap is collection wiring, not source discovery.

**P1 — `known_scams` feed is dead (29 days).**
26,720 points, no new data since 2026-08-30. The scam pipeline is fed by
`scamsniffer/scam-database` and `CryptoScamDB/blacklist`, both cloned in-tree.
*Concrete source:* `systemctl list-timers | grep -Ei 'rag|ingest'` → the unit is not firing.

**P1 — `scan_intel` 4096 twin is empty while base holds 46,745.**
The canonical 8B lane reads **nothing** for this corpus. Either the twin was never backfilled or
the write path never targeted it. *Concrete source:* base has points, twin has 0, same payload shape.

**P1 — `rug_events` (1,877) is stranded at 2560 with no twin.**
Cannot be written to by the canonical lane (dim guard would refuse 4096 into a 2560 collection) and
cannot be read by a 4096 query. Needs a twin created or a documented 2560 read path.

**P2 — research/whitepapers are ~1,259 documents fleet-wide.**
`research_v2` 158 · `contract_audits` 902 · `github_audits` 199 · `standards` 0. Against 4.26M code
points, paper-shaped content is 0.03%. *Concrete source:* arXiv/cryptology feed is not in the
ingest list; `research_v2` has a live twin and 3d freshness, so the pipeline works and the source
is absent.

**P2 — market data is 2,393 points across three collections, and `market_health` is stale.**
`market_data` 467 (BLIND), `market_health` 627 (**17d stale**), `stablecoin_market` 336 (BLIND).
2,393 points total against a 6.3M-point fleet.
ClickHouse is the source per `docs/DATA-PLACEMENT.md`; it clearly is not feeding at volume.

**P2 — `regulation_enforcement` is 1320 days old.**
Newest content 2023-02-15. It is historical-only and is *labelled* as such in
`docs/COLLECTION_REGISTRY.md` — so this is a **disclosure** item, not a bug. Enforcement actions
since Feb 2023 are unindexed.

**P3 — language coverage gaps (real but low blast radius).**
`java` 127 · `move` 125 · `c` is 1,133 non-crypto docs (dovecot/postfix/neomutt) ·
`markdown` 0.81% and 23% of it is our own `internal/standards`. Move and Java are effectively
unindexed for crypto. Given the fleet is EVM/Solana-first this is expected, not urgent.

**P3 — 21 empty scaffolds.**
`bridge_architecture`, `mev_architecture`, `zk_circuits`, `l2_risk`, `crime_reports`,
`liquidation_events`, `token_unlocks`, `wallet_clusters`, `youtube_videos`, `hyperliquid`,
`exchange_flows`, `scanner_memory`, `scam_signatures`, `sp_tailtest`, `mem0migrations`,
`solana_patterns`, `coding_knowledge`, `scams_v2`, `zk_circuits`, `security_patterns_test`(has 92),
`does_not_exist`. Each is a declared capability with no data. `does_not_exist` is a **test fixture**
(verified: `tests/test_ingest_sources.py` asserts `get_source("does_not_exist") is None`) — it is
harmless but it is a real collection in a production cluster and it has no `_8b` twin.

---

## 7. What I could NOT measure

Explicitly listed, because a wrong number here propagates into an ingest plan.

1. **Exact language distribution.** 401,408 of 2,130,842 points sampled (18.8%), sequential from the
   head of the collection. If the tail is language-skewed the true percentages move. **Head
   languages (±0.1pp) are solid. Sub-0.1% languages (`aiken` 84, `func` 79, `cpp` 59, `dart` 61,
   `ligo` 70, `dockerfile` 1) are indicative only** — I am not asserting "0.00%" for any of them.
2. **"Zero coverage" is a claim about the sample, not the corpus.** I can say *no `zig` or `elixir`
   appeared in 401,408 points*. I cannot say the corpus contains none. The true test is a
   facet-count over all 2.13M, which Qdrant does not expose via this API.
3. **Content dates vs ingest dates.** 48 collections have neither. Every "freshness" value in §5 for
   a BLIND collection is absent, not inferred. I did not substitute content dates for ingest dates
   — the repo's own checker explains why that would be wrong (§8).
4. **Whether the 4 stale feeds are *supposed* to be live.** `news_articles` may be deliberately
   retired in favour of `news_v2`; I have no unit/cron evidence either way. I ran no systemd query.
5. **The 2560 → 4096 migration state per collection.** Point drift (§1.3) is observed, but whether a
   given gap means "mid-migration", "backfill pending" or "twin deliberately not fed" is not
   recoverable from Qdrant.
6. **Why `codebase_reference_v2` has no ingest timestamp** — I did not trace the full write path in
   `store_write.py`; I confirmed the field is absent from sampled payloads.
7. **Deduplication semantics.** `rag_documents` and `news_v2` have their 4096 twin *ahead* of the
   base by 24–26 points. Whether that is in-flight ingest, a re-embed, or drift, is unmeasured.
8. **Collection *intention*.** I read `docs/COLLECTION_REGISTRY.md` and the ingest scripts for
   `does_not_exist` and `audit_findings`. For the other 19 empty collections I did not trace each
   one's source wiring, so "never fed" is a measurement, not a diagnosis of why.
9. **Anything about the `_8b` *embedder*.** I verified the collections are 4096-dim. I did not verify
   the embedder serving :8004 is actually producing 8B output right now.
10. **Non-Qdrant stores.** ClickHouse (39M address labels per the fleet docs), Postgres, Neo4j
    (`wallet_clusters` is 0 in Qdrant but the fleet claims 169K+ labels in Neo4j). I measured Qdrant
    only. **A Qdrant gap is not necessarily a fleet gap** — wallet labels may be healthy in Neo4j.

---

## 8. The evidence this report rests on

`scripts/check-collection-staleness.py` docstring, verbatim, because it invalidates the obvious
method:

> CONTENT DATES ARE NOT INGEST DATES — Content dates ("when the exploit happened",
> `metadata.due_date`) are properties of the world, not of the corpus. Measured: `rag_documents` and
> `defi_exploits` both carry content dates of 2026-12-08 - eleven days in the FUTURE - which read
> as "fresh". Only a field the ingester stamps on arrival answers "when was this collection last fed".

> MISSING TIMESTAMPS ARE REPORTED, NOT FAILED — 30 of 40 crypto collections carry no ingest-side field
> at all. That is a real instrumentation gap, but a permanently-red gate gets muted and a muted gate
> is worse than none.

My own measurement puts that number at **48 of 61** today, up from 30 of 40 on 2026-09-27. The gap
is **widening**, not closing.

`src/rugbrain/dim_policy.py`, verbatim, on the no-overwrite rule:

> Retired generations, kept for reading, never written to by the canonical lane:
> 2560 = qwen3-embedding-4b (retained base collections, no-overwrite rule)

## 9. Reproduce

```bash
# inventory (GET only)
curl -s http://127.0.0.1:6334/collections | python3 -m json.tool
for c in $(curl -s http://127.0.0.1:6334/collections | python3 -c \
  'import json,sys;print(" ".join(x["name"] for x in json.load(sys.stdin)["result"]["collections"]))'); do
  curl -s "http://127.0.0.1:6334/collections/$c" | \
    python3 -c 'import json,sys;r=json.load(sys.stdin)["result"];v=r["config"]["params"]["vectors"];print(r["status"], {k:d["size"] for k,d in v.items()} if isinstance(v,dict) else v["size"])'
done

# exact counts (POST is read-only; GET /count ignores exact=true)
curl -s -XPOST http://127.0.0.1:6334/collections/<name>/points/count \
  -H 'Content-Type: application/json' -d '{"exact":true}'

# freshness (authoritative, read-only)
python3 scripts/check-collection-staleness.py

# registry (regenerates docs/COLLECTION_REGISTRY.md — do NOT run in a dirty tree)
python3 scripts/registry_report.py
```
