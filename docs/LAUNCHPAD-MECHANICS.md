# Launchpad Mechanics — corpus-grounded reference

Scope: one-page mechanism brief per platform **we hold material on**. Every claim carries a
citation to a `library_id` + `file_path` in the ContextCrypto corpus. Anything the corpus does
not support is written `UNKNOWN — not in corpus`; nothing is filled from memory.

Corpora searched: `docs:pumpfun`, `docs:bags`, `docs:zora`, `docs:raydium`, `docs:meteora`,
`chainstacklabs/pumpfun-bonkfun-bot`, `nirholas/pump-fun-sdk`, `ourzora/zora-protocol`,
`GMGNAI/gmgn-skills`, `internal/lite-chain-indexer`, `internal/degenfeed-web`.

---

## 1. pump.fun — Solana

| Field | Value | Citation |
|---|---|---|
| Create-coin cost | 0 SOL | `docs:pumpfun` · `https://pump.fun/docs/fees` |
| Curve trade fee | 1% of the SOL value of each buy/sell while on the curve | `docs:pumpfun` · `https://pump.fun/docs/fees` |
| Graduation cost | 6 SOL fixed, **taken from the coin's liquidity** (network + DEX fees included), not paid by the user | `docs:pumpfun` · `https://pump.fun/docs/fees` |
| Curve formula | Constant product `x * y = k`; `x` = **virtual** SOL reserves, `y` = **virtual** token reserves | `nirholas/pump-fun-sdk` · `.github/skills/bonding-curve.skill.md` |
| Graduation trigger | `realTokenReserves` reaches zero → `bondingCurve.complete = true`; threshold read from the `Global` account | `nirholas/pump-fun-sdk` · `docs/bonding-curve-math.md`; `.github/skills/token-lifecycle.skill.md` |
| Migration destination | **PumpSwap AMM** (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`); permissionless `migrate` creates the pool and burns LP. Legacy Raydium path "effectively retired". | `docs:pumpfun` · `https://docs.chainstack.com/docs/solana-listening-to-pumpfun-migrations-to-raydium` |
| Reserve accounting at migration | The 30 SOL virtual advance is reclaimed: total SOL reserve 115 → 85 SOL; price is continuous across the transition | `docs:pumpfun` · `https://arxiv.org/html/2602.14860v1` |
| Fee split (creator/protocol/referrer) | Fee-share list takes `basic_points` in **bps summing to 10000**, providers `solana` / `twitter` / `github` | `GMGNAI/gmgn-skills` · `skills/gmgn-cooking/SKILL.md` |
| Fee-split **percentages** | UNKNOWN — not in corpus (the bps vocabulary is cited; the default creator/protocol split is not) | — |
| Total supply / virtual token reserve / current graduation SOL threshold | UNKNOWN — not in corpus | — |

**Program openness:** program source closed; IDLs vendored from the official
`pump-fun/pump-public-docs` (`idl/pump.json`, `pump_amm.json`, `pump_fees.json`) —
`chainstacklabs/pumpfun-bonkfun-bot` · `docs/pumpfun-protocol.md`.

**Detect on-chain:** pump.fun `create` instruction (exact 8-byte discriminator) → launch; PumpSwap
`create_pool` → graduation. Implemented as exact discriminator match (`high` confidence) in
`internal/lite-chain-indexer` · `lite_indexer/strategies/solana/launch.py`
(`VenueSpec.launch_discriminators`, `graduation=True`).

---

## 2. letsbonk.fun (Bonk) — Solana

| Field | Value | Citation |
|---|---|---|
| Underlying program | **Raydium LaunchLab** `LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj` — letsbonk.fun and StonkFun are *pools on one program*, not separate protocols | `chainstacklabs/pumpfun-bonkfun-bot` · `docs/launchlab-protocol.md` |
| Curve type | Program-enforced curve chosen from supported shapes (logistic/exponential); platforms **cannot** add arbitrary custom curves | `docs:raydium` · `https://docs.raydium.io/llms-full.txt` |
| Graduation trigger | `quote_vault.balance >= graduation_threshold`; threshold set at `Initialize`, program-enforced | `docs:raydium` · `https://docs.raydium.io/llms-full.txt` |
| Migration destination | Raydium AMM pool, **CPMM or CLMM**; remaining curve supply + collected quote tokens migrate | `docs:raydium` · `https://docs.raydium.io/llms-full.txt` |
| Launch instruction | `initialize_v2` (name-matched); LetsBonk additionally identified by a required config account | `internal/lite-chain-indexer` · `lite_indexer/strategies/solana/launch.py` |
| Token creation params | UNKNOWN — not in corpus | — |
| Fee % and creator/protocol/referrer split | **Partially unknown** — Raydium documents a creator-fee stream on the curve and after graduation (`/user-flows/how-creator-fees-work`), but the numeric bps were not in the retrieved corpus text | `docs:raydium` · `https://docs.raydium.io/llms-full.txt` (pointer only) |

**Program openness:** program is on-chain and its **IDL is pulled from the program's on-chain IDL
account** (`idl/raydium_launchlab_idl.json`) — `chainstacklabs/pumpfun-bonkfun-bot` ·
`docs/launchlab-protocol.md`. Raydium docs state every platform inherits LaunchLab's audit posture
and cannot bypass it (`docs:raydium`).

**Detect on-chain:** program ID + `initialize_v2` (launch, `medium` confidence by name) and the
LetsBonk config-account presence (exact, `high`) — `internal/lite-chain-indexer` ·
`lite_indexer/strategies/solana/launch.py`.

---

## 3. Zora — Base (EVM)

| Field | Value | Citation |
|---|---|---|
| Token standard / chain | ERC-20 "media coins" on **Base (8453)**; Base Sepolia (84532) testnet, where `ZORA` currencies are unavailable | `ourzora/zora-protocol` · `docs/public/skill.md` |
| Liquidity venue | Every coin has a **Uniswap V4 pool with locked liquidity** — no bonding curve, no graduation event | `ourzora/zora-protocol` · `docs/public/skill.md` |
| Creator Coin | Supply **1,000,000,000**; **50%** vests to creator over 5 years, **50%** tradeable at launch; **1%** trading fee | `ourzora/zora-protocol` · `docs/public/skill.md`; `docs:zora` · `https://docs.zora.co/llms-full.txt` |
| Content Coin | Supply 1B; **10M** immediate creator allocation, **990M** tradeable; **1%** trading fee | `docs:zora` · `https://docs.zora.co/llms-full.txt` |
| Trend Coin | **0.01% (1 bps)** fee; **99% sniper tax at launch decaying linearly to 0.01% over 10 days** | `docs:zora` · `https://docs.zora.co/llms-full.txt` |
| Fee split | **1% split across creator, referrers, protocol and LP** — four buckets named; exact bps per bucket UNKNOWN — not in corpus | `docs:zora` · `https://docs.zora.co/llms-full.txt` |
| Graduation / migration | Not applicable by design (V4 pool at launch, liquidity locked). Hook-version liquidity migration exists (`BaseCoinV4` "handles liquidity migration between hook versions") | `docs:zora` · `https://docs.zora.co/llms-full.txt` |
| Our own wrapper caps | platform fee ≤50 bps, creator fee ≤200 bps, creator allocation ≤1500 bps | `internal/degenfeed-web` · `packages/zora-sdk/src/evm.ts` |

**Program openness:** open — `ourzora/zora-protocol` is a public repo carrying the protocol
skill/docs we cite.

**Detect on-chain:** Base 8453; index Uniswap V4 `PoolManager` `Initialize`/`Swap` logs plus
`BaseCoinV4`/`CreatorCoin` deployments rather than a launchpad factory — `ourzora/zora-protocol` ·
`docs/public/skill.md`.

---

## 4. bags.fm — Solana

| Field | Value | Citation |
|---|---|---|
| Venue model | Trades return `venue: "curve"` with `priceEthPerToken`, `ethWei`, `tokenWei`, `blockNumber`, `logIndex` — a curve venue is exposed through the public trades API | `docs:bags` · `https://docs.bags.fm/api-reference/get-rh-trades` |
| Public API base | `https://public-api-v2.bags.fm/api/v1` | `0xD3bt/Trench-Tools` · `rust/launchdeck-engine/src/main.rs` |
| Fee-split capability | A "fee split" object is a first-class launch parameter (`isBagsFeeSplitLaunchpad`, split-draft normalisation, deployer fee-split address) | `0xD3bt/Trench-Tools` · `extension/trench-tools/launchdeck/app/split-editors-domain.js` |
| Token creation params | UNKNOWN — not in corpus | — |
| Curve / price formula | UNKNOWN — not in corpus | — |
| Fee split percentages (creator/protocol/referrer) | UNKNOWN — not in corpus | — |
| Graduation trigger + destination | UNKNOWN — not in corpus | — |

**Program openness:** UNKNOWN — not in corpus. Only the HTTP API surface and a third-party
client's fee-split UI are held; no program ID or IDL.

**Detect on-chain:** UNKNOWN (no program ID in corpus). Off-chain, the trades endpoint above is
the only documented signal we hold.

---

## 5. SunPump — Tron

| Field | Value | Citation |
|---|---|---|
| Creation event | `TokenCreate` log emitted by the SunPump contract; token address in the first data word (`41` + word[24:64]), creator and token index decoded from topics/data | `internal/lite-chain-indexer` · `lite_indexer/strategies/tron/launch.py` (`detect_sunpump_launch`) |
| Graduation | A `launchToDEX` call on the SunPump contract seeds the token onto **SunSwap**; the call's single address argument names the token | `internal/lite-chain-indexer` · `lite_indexer/strategies/tron/launch.py` (`detect_graduation`) |
| Contract address value | UNKNOWN — referenced as the `SUNPUMP_CONTRACT` constant; value not in retrieved corpus text | — |
| Token creation params | UNKNOWN — not in corpus | — |
| Curve / price formula | UNKNOWN — not in corpus | — |
| Fee split | UNKNOWN — not in corpus | — |
| Graduation threshold | UNKNOWN — not in corpus | — |

**Program openness:** UNKNOWN — not in corpus.

**Detect on-chain:** TronGrid event polling → `TokenCreate` topic (launch) and `launchToDEX`
selector (graduation), both implemented as pure receipt/tx matchers in
`internal/lite-chain-indexer` · `lite_indexer/strategies/tron/launch.py`.

---

## 6. Adjacent: Meteora DBC (fee-split reference model)

| Field | Value | Citation |
|---|---|---|
| Protocol fee | **20%** of the total trading fee | `docs:meteora` · `https://docs.meteora.ag/llms-full.txt` |
| Referral fee | **20% of the protocol fee** (≈4% of total) | `docs:meteora` · `https://docs.meteora.ag/llms-full.txt` |
| Creator vs partner | `Creator Fee = LP Fee × Creator Trading Fee %`; `Partner Fee = LP Fee − Creator Fee` | `docs:meteora` · `https://docs.meteora.ag/llms-full.txt` |
| Migration fee | UNKNOWN — heading present, value not in retrieved text | — |

Included because it is the only **numeric, fully-specified four-way split** in the corpus, and the
right template for describing any launchpad fee split precisely.

---

## 7. Comparison

| Platform | Chain | Curve type | Fee % | Graduation trigger | Migration target | Source posture |
|---|---|---|---|---|---|---|
| pump.fun | Solana | Constant product `x·y=k`, virtual reserves | 1% trade; 0 SOL create; 6 SOL graduation | `realTokenReserves = 0` / `complete = true` | PumpSwap AMM (`pAMM…FMfXEA`) | Closed program, public IDLs |
| letsbonk.fun | Solana | Raydium LaunchLab program-enforced curve | UNKNOWN (creator-fee stream documented, bps not) | `quote_vault.balance ≥ graduation_threshold` | Raydium CPMM or CLMM | On-chain IDL; inherited audits |
| Zora | Base 8453 | None — Uniswap V4 pool at launch | 1% (Creator/Content), 0.01% + sniper tax (Trend) | N/A | N/A (locked V4 liquidity) | Open (`ourzora/zora-protocol`) |
| bags.fm | Solana | UNKNOWN (venue `"curve"` exists) | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| SunPump | Tron | UNKNOWN | UNKNOWN | `launchToDEX` call | SunSwap | UNKNOWN |
| Meteora DBC (ref) | Solana | Dynamic bonding curve | protocol 20% of trading fee; referral 20% of protocol | UNKNOWN | UNKNOWN | Docs only |

---

## 8. Gap list — what is unknown and which source would close it

| # | Gap | Closes with |
|---|---|---|
| 1 | pump.fun default creator/protocol/referrer split in bps, total supply, virtual token reserve, current graduation SOL threshold | `pump-fun/pump-public-docs` IDL + `Global` account decode; refresh `docs:pumpfun` fee page (it still says "graduates to Raydium", contradicting PumpSwap) |
| 2 | letsbonk / LaunchLab numeric fee bps and per-platform customisation | `docs.raydium.io/user-flows/how-creator-fees-work` full page; LaunchLab `GlobalConfig` account |
| 3 | bags.fm creation params, curve formula, fee bps, graduation and destination | `docs.bags.fm` (docs **are** in corpus — widen the crawl to fees/launch pages); Bags program ID + IDL |
| 4 | SunPump contract address, creation params, curve, fee split, threshold | Tron scan of `SUNPUMP_CONTRACT` in `lite_indexer/strategies/tron/launch.py`; sunpump.meme docs |
| 5 | Zora per-bucket fee bps (creator/referrer/protocol/LP) and Trend-coin decay window | `docs.zora.co` fee page; `ourzora/zora-protocol` fee-split contract source |
| 6 | Meteora DBC migration fee | `docs.meteora.ag` migration section |
| 7 | Whether any figure changed after corpus capture | re-crawl `docs:*` launchpad libraries; every number above is a snapshot, not live |

**Rule going forward:** a mechanism number without a `library_id` + `file_path` here is not a fact —
it is a gap. Extend this table, do not extend the prose.
