#!/usr/bin/env python3
"""Curated crypto-expert knowledge for the RugBrain chatbot.

Supplements the seeded collections with high-signal educational content the
chatbot can cite when users ask about:

  - DeFi protocols and strategies (yield farming, lending, staking, DEX)
  - Token analysis fundamentals (market cap, FDV, tokenomics, liquidity)
  - Market analysis and cycles (halving, alt-season, on-chain indicators)
  - Cross-chain comparisons (Solana vs Ethereum, L2 rollups, bridges)
  - MEV and sandwich attacks (mechanics + protection)
  - Smart contract security patterns (defensive patterns + audit case studies)
  - Wallet security best practices (hardware wallets, approvals, phishing)

Each doc carries a ``collection`` hint so the ingester
(``ingest_crypto_expert_knowledge.py``) routes it to the canonical RAG
collection:

  - ``education``        — general protocol / token / market / wallet knowledge
  - ``coding_knowledge`` — code-adjacent guidance (cross-chain dev, wallet dev)
  - ``contract_security``— MEV + smart-contract defensive patterns
  - ``audit_findings``   — real audit case studies (Euler, Mango, Nomad, ...)

Point IDs are deterministic (source + rel_key + content hash) so re-runs
overwrite the same Qdrant points instead of duplicating them.
"""

from __future__ import annotations

from typing import Any

EXPERT_DOCS: list[dict[str, Any]] = [
    # ═══════════════════════════════════════════════════════════════
    # DEFI PROTOCOLS AND STRATEGIES
    # ═══════════════════════════════════════════════════════════════
    {
        "source": "crypto-expert",
        "rel_key": "defi/yield-farming",
        "title": "Yield farming — how DeFi returns work",
        "topic": "defi-strategies",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Yield farming is the practice of supplying capital to DeFi "
            "protocols in exchange for rewards. The four core return sources: "
            "1) Trading fees — LP positions earn a share of swap fees "
            "(e.g. 0.3% per swap on a classic AMM), proportional to your "
            "share of the pool. 2) Lending interest — suppliers to lending "
            "markets earn borrow rates paid by borrowers, usually variable. "
            "3) Protocol incentives — extra governance tokens (emissions) "
            "paid to liquidity providers to bootstrap depth; these are often "
            "the largest but most inflationary component. 4) Staking "
            "rewards — locking native tokens for network security or "
            "protocol revenue. Distinguish APY (annualized, compounding) "
            "from APR (simple, no compounding). Always account for "
            "impermanent loss, token emissions dilution, and smart-contract "
            "risk: a high headline APY funded by printing tokens is usually "
            "a declining-real-value yield."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "defi/impermanent-loss",
        "title": "Impermanent loss — the LP cost you can't see",
        "topic": "defi-strategies",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Impermanent loss (IL) is the opportunity cost liquidity providers "
            "suffer when the price ratio of the two pooled assets changes. In "
            "a constant-product AMM (x*y=k), as one token rises relative to "
            "the other, arbitrageurs trade against the pool until it "
            "rebalances, so the LP ends up holding more of the depreciating "
            "asset and less of the appreciating one. If the price doubles, "
            "IL is ~5.7%; if it halves, ~20% vs. just holding. IL becomes "
            "permanent once you withdraw. Mitigations: concentrate "
            "liquidity around the current price (Uniswap v3), pair "
            "correlated assets (stablecoin pairs, ETH/wstETH), or farm pools "
            "whose fee income + emissions exceed expected IL. Diversifying "
            "across volatility regimes and monitoring pool composition are "
            "core LP risk management."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "defi/lending-markets",
        "title": "DeFi lending — how Compound/Aave markets work",
        "topic": "defi-protocols",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "DeFi lending protocols (Compound, Aave, Spark) run "
            "over-collateralized money markets. Suppliers deposit assets and "
            "receive interest-bearing tokens (cTokens, aTokens) whose value "
            "grows with the supply rate; borrowers post collateral and take "
            "loans in other assets at a borrow rate. The utilization ratio "
            "(borrowed / supplied) drives rates — higher utilization means "
            "higher APY. Loans must stay above the liquidation threshold "
            "(typically 75-85% loan-to-value); if collateral falls below it, "
            "liquidators repay part of the loan and claim a bonus (5-10%), "
            "which can cascade in market crashes. Key risks: oracle "
            "manipulation on illiquid collateral, depeg events, and "
            "governance-controlled parameters. Health factor = collateral * "
            "liquidation threshold / debt — keep it comfortably above 1.1."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "defi/amm-mechanics",
        "title": "AMM mechanics — how DEX liquidity pools price assets",
        "topic": "defi-protocols",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Automated market makers (AMMs) replace order books with "
            "algorithmic pricing. The constant-product curve x*y=k means the "
            "product of reserves stays fixed: a buy removes token A and adds "
            "token B, moving the price along the curve. Price impact grows "
            "with trade size relative to pool depth — large trades on thin "
            "pools get terrible execution. Variants: constant-sum (stables), "
            "constant-product (Uniswap v2, most pairs), concentrated "
            "liquidity (Uniswap v3 lets LPs set price ranges for higher "
            "efficiency), stableswap curves (Curve, low slippage for "
            "correlated assets), and dynamic-fee or TWAMM designs. When "
            "analyzing a token, pool depth and concentration matter more "
            "than raw volume: a deep, widely distributed pool is harder to "
            "manipulate and safer to trade."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "defi/stablecoin-strategies",
        "title": "Stablecoin strategies — yield, depeg risk, and safety",
        "topic": "defi-strategies",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Stablecoins fall into three classes with very different risk "
            "profiles. Fiat-backed (USDC, USDT, PYUSD): redeemable 1:1 for "
            "cash, risk is custodial solvency and freezing. Crypto-"
            "collateralized (DAI, USDe): over-collateralized or delta-neutral "
            "positions; risk is collateral volatility and depeg in stress. "
            "Algorithmic (UST, USDD, FRAX models): no full backing, risk is "
            "death spirals when confidence breaks — UST collapsed to near "
            "zero in May 2022. Yield strategies: lend stables on Aave for "
            "5-15% APR, provide Curve/Uniswap stable pools, or deposit into "
            "vaults (Yearn, beefy). Safety checklist: check the backing "
            "mechanism and audits, monitor reserve transparency, avoid "
            "exotic yields far above the market rate, and treat any "
            "'guaranteed 20%+ on stables' as a red flag."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "defi/leverage-liquidations",
        "title": "Leverage and liquidations — how leverage cascades",
        "topic": "defi-strategies",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "DeFi leverage multiplies exposure via borrowing, perps, or "
            "leveraged vaults, and it cuts both ways. A 3x long position "
            "needs price to move only ~30% against it to be fully "
            "liquidated (minus liquidation fees and slippage). Liquidation "
            "cascades happen when many leveraged positions cluster at the "
            "same price level: forced sells push price down, triggering more "
            "liquidations — the classic March 2020 and May 2021 events. "
            "Perpetual DEXs (dYdX, Hyperliquid, GMX) use funding rates to "
            "keep perp price near spot; extreme positive funding signals "
            "crowded longs. When evaluating a token's crash risk, look at "
            "aggregate open interest, funding, and the distance to large "
            "liquidation clusters — a token with huge leverage overhead is "
            "one sharp move away from a waterfall."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "defi/restaking",
        "title": "Restaking (EigenLayer) — economics and risk",
        "topic": "defi-protocols",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Restaking lets staked ETH (or SOL via similar designs) secure "
            "additional services — AVSs (actively validated services) — "
            "earning extra yield on top of base staking. EigenLayer is the "
            "canonical example: users deposit LSTs (stETH, rETH) or native "
            "ETH into restaking and operators run AVS tasks; slashing "
            "conditions punish misbehavior, creating a security market. The "
            "tradeoff: compounding risk — the same capital now secures "
            "multiple networks, so a bug in one AVS can slash the restaker. "
            "Risk checklist: AVS maturity and audits, operator quality, "
            "slashing terms, and the withdrawal queue (restaked positions "
            "have long unlock windows). Restaking is a legitimate yield "
            "source but a concentrated risk stack — size positions "
            "accordingly."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "defi/dca-and-exits",
        "title": "DCA and exit strategies — position sizing basics",
        "topic": "defi-strategies",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Dollar-cost averaging (DCA) buys a fixed dollar amount on a "
            "fixed schedule, smoothing entry price over time and removing "
            "emotion. DCA into volatile crypto assets reduces the risk of "
            "buying tops and is the standard accumulation strategy through "
            "bear markets. Exit discipline matters equally: predefine target "
            "prices or % gains, scale out in tranches (e.g. sell 25% at +50%, "
            "25% at +100%, hold the rest), and use trailing stops to let "
            "winners run. Position sizing rule of thumb: never risk more "
            "than 1-2% of portfolio on any single speculative position; "
            "meme coins and low-cap tokens deserve smaller sizes by "
            "construction. An entry plan without an exit plan is gambling — "
            "write both before buying."
        ),
    },
    # ═══════════════════════════════════════════════════════════════
    # TOKEN ANALYSIS FUNDAMENTALS
    # ═══════════════════════════════════════════════════════════════
    {
        "source": "crypto-expert",
        "rel_key": "token/market-cap-fdv",
        "title": "Market cap vs fully diluted valuation (FDV)",
        "topic": "token-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Market capitalization (price x circulating supply) and fully "
            "diluted valuation (price x total max supply) answer different "
            "questions. Circulating cap reflects the value of what is "
            "currently tradeable; FDV reflects the value if every token "
            "were outstanding. A token with a 50M circulating supply and a "
            "1B max supply trades at an FDV 20x its market cap — future "
            "emissions, unlocks, and team/VC allocations will dilute "
            "holders. Checklist when analyzing: 1) what fraction is "
            "unlocked vs locked, 2) unlock schedule — cliffs and monthly "
            "emissions, 3) where locked tokens go (team, treasury, VCs, "
            "foundation), 4) the ratio of FDV to comparable projects — an "
            "FDV above category leaders with a young protocol is a warning "
            "sign. Low-float-high-FDV listings often dump when unlocks "
            "arrive."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "token/tokenomics",
        "title": "Tokenomics — supply, inflation, and value accrual",
        "topic": "token-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Tokenomics is the economic design of a token: supply schedule, "
            "distribution, and how value accrues to holders. Inflationary "
            "tokens (emissions, staking rewards) dilute holders unless "
            "buyback-and-burn or fee-splitting mechanisms offset it. "
            "Deflationary mechanisms (burns, fee redistribution) support "
            "price but only matter if demand holds. Value accrual models: "
            "1) fee capture — protocol revenue paid to stakers (GMX, "
            "Hyperliquid), 2) buyback-and-burn (BNB, some DEX tokens), "
            "3) governance-only (most DAO tokens — value is vote power), "
            "4) utility (gas, collateral, payment). Distribution matters: "
            "concentrated early allocation to team/VCs with short cliffs "
            "incentivizes dumps; wide community distribution with long "
            "vesting supports stability. Always read the tokenomics page "
            "before investing — it explains who gets paid and when."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "token/liquidity-analysis",
        "title": "Liquidity analysis — depth, concentration, and risk",
        "topic": "token-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Token liquidity determines how much you can trade without "
            "moving price and is a first-order rug/exit risk metric. "
            "Analyze: 1) total liquidity across DEXs and the largest pool, "
            "2) liquidity / market cap ratio — below 5-10% is fragile, "
            "3) liquidity concentration — a single LP holder with a large "
            "share can pull the rug (check LP ownership, whether LP tokens "
            "are burned or locked), 4) pool age and volume history — brand "
            "new pools with high volume can be wash-traded, 5) buy/sell "
            "slippage at moderate sizes (5-10% of pool depth). A rug pull "
            "typically removes liquidity, so the 'can I actually exit?' "
            "question is answered by depth analysis, not hype. Tools: "
            "DexScreener pair info, DEX tools liquidity views, and on-chain "
            "LP holders."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "token/holder-distribution",
        "title": "Holder distribution — concentration and whale risk",
        "topic": "token-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Holder distribution shows who owns a token. High concentration "
            "is a systemic risk: if the top 10 holders control 60%+ of "
            "supply, a single whale or the team can dump the market. "
            "Analysis checklist: 1) top-10 and top-100 holder share, 2) "
            "deployer and team wallet balances, 3) exchange wallet "
            "balances (large exchange holdings = potential sell pressure), "
            "4) liquidity provider ownership, 5) new wallet creation rate "
            "(organic growth vs airdrop farming). Warning signs: the "
            "deployer holds a large unvested share, addresses are clustered "
            "(one entity split across many wallets), or top holders are "
            "uncategorized fresh wallets. Healthy tokens have broad, "
            "distributed ownership with a clear, vested team allocation. "
            "Tools: Etherscan/Solscan holder pages, whale alert services, "
            "and chain analytics dashboards."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "token/unlocks-and-emissions",
        "title": "Token unlocks and emissions — the sell-pressure calendar",
        "topic": "token-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Token unlock schedules are the single most predictable source "
            "of sell pressure. Typical structures: 1) TGE (token generation "
            "event) with a portion tradeable immediately, 2) cliff — a "
            "period (3-12 months) before any unlocked supply vests, 3) "
            "linear vesting — X% unlocks daily/monthly after the cliff. "
            "Watch for: large cliffs 3-6 months after TGE (team + VC "
            "allocations), daily emissions that dwarf buy volume, and "
            "'staked to earn more' loops that compound sell pressure. "
            "Compare unlock volume against daily trading volume: an unlock "
            "worth 50% of daily volume is absorbable; one worth 500% "
            "usually dumps. Tools: TokenUnlocks, crypto token vesting "
            "dashboards, and on-chain treasury monitoring. Price often "
            "front-runs unlocks — the market prices in the known dump "
            "months ahead."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "token/onchain-metrics",
        "title": "On-chain metrics — NVT, active addresses, velocity",
        "topic": "token-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "On-chain metrics ground price analysis in real network usage. "
            "Core indicators: 1) active addresses — unique senders/receivers "
            "per day; sustained growth = real adoption, spikes with no "
            "price action can be wash activity, 2) transaction count and "
            "value transferred, 3) NVT (network value to transactions) — "
            "market cap / on-chain volume; high NVT = overvalued relative "
            "to usage, low = undervalued, 4) token velocity — how often a "
            "unit changes hands; high velocity correlates with weak price "
            "(tokens are spent, not held), 5) new vs returning users, 6) "
            "exchange flows — net inflows to exchanges signal sell "
            "intent. Combine on-chain metrics with price for confirmation; "
            "a rally on falling active addresses is fragile. Data sources: "
            "blockchain explorers, CoinGecko/CryptoQuant dashboards, "
            "Glassnode-style analytics."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "token/meme-token-vs-bluechip",
        "title": "Meme tokens vs blue chips — a comparison framework",
        "topic": "token-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Blue chips (BTC, ETH, SOL) and meme tokens (DOGE, SHIB, "
            "newer community coins) are different asset classes. Blue "
            "chips: established security models, deep liquidity, "
            "institutional flows, macro sensitivity — they behave like "
            "risk assets with fundamental floors. Meme tokens: community "
            "and narrative driven, extreme volatility, tiny/no fundamental "
            "value — they are attention assets whose price is driven by "
            "social momentum, exchange listings, and celebrity/retail "
            "waves. Framework for either: liquidity depth, holder "
            "distribution, unlock schedules, trading volume sustainability, "
            "and team transparency. Meme tokens demand smaller position "
            "sizes, strict profit-taking, and acceptance that the "
            "narrative can vanish overnight. Never confuse 'it went up' "
            "with 'it has value' — differentiate momentum from "
            "fundamentals."
        ),
    },
    # ═══════════════════════════════════════════════════════════════
    # MARKET ANALYSIS AND CYCLES
    # ═══════════════════════════════════════════════════════════════
    {
        "source": "crypto-expert",
        "rel_key": "market/halving-cycles",
        "title": "Bitcoin halving cycles — the 4-year rhythm",
        "topic": "market-cycles",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Bitcoin's issuance halves roughly every 210,000 blocks (~4 "
            "years): 50 → 25 → 12.5 → 6.25 → 3.125 BTC per block. The "
            "halving cuts new supply, and historically the market has "
            "priced in the supply shock over the following 12-18 months, "
            "with a cycle peak roughly 12-18 months after the halving, "
            "followed by a deep bear. Caveats: each cycle has different "
            "macro conditions (2016: retail+China, 2020: COVID liquidity, "
            "2024: ETFs + institutional), and diminishing returns — each "
            "halving's marginal supply cut matters less relative to the "
            "growing float. Use halving as a timing context, not a "
            "guarantee: combine with on-chain cost basis (MVRV, SOPR), "
            "funding rates, and macro liquidity. The 4-year rhythm still "
            "shapes sentiment even if it no longer dictates price alone."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "market/altseason",
        "title": "Altcoin season — how alt rotation works",
        "topic": "market-cycles",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Altcoin season is the period when altcoins outperform Bitcoin "
            "(typically measured as 75% of the top 50 alts beating BTC over "
            "90 days). The typical sequence: BTC leads (new capital flows "
            "to the 'safest' crypto), then ETH and majors catch up, then "
            "mid-caps, then small caps and meme coins — liquidity "
            "rotates down the risk curve as greed builds. Triggers: BTC "
            "consolidating after a leg up, stablecoin supply expansion, "
            "narrative cycles (DeFi summer, NFT mania, AI tokens, meme "
            "waves). Signals to watch: BTC dominance falling, ETH/BTC "
            "rising, alt volume share climbing, and funding turning "
            "positive. Altseason ends when BTC dominance reverses and "
            "small caps bleed first. Trade it with defined sizes and "
            "take-profits — the rotation down is faster than the rotation "
            "up."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "market/onchain-cycle-indicators",
        "title": "On-chain cycle indicators — MVRV, SOPR, realized cap",
        "topic": "market-cycles",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "On-chain cycle indicators estimate whether the market is "
            "euphoric or capitulating. MVRV (market value / realized value) "
            "compares current price to the average cost basis of all coins: "
            "MVRV > 3.5-4 historically marks late-cycle euphoria (large "
            "profit-taking overhang), MVRV < 1 marks prices below aggregate "
            "cost basis — historically near bottoms. SOPR (spent output "
            "profit ratio) tracks whether coins moved at a profit or loss: "
            "SOPR below 1 during a crash = capitulation; sustained >1 = "
            "profitable hands dominate. Realized cap (value of all coins at "
            "the price they last moved) is the 'true' stored value vs. "
            "market cap. Combine: euphoric MVRV + high SOPR + parabolic "
            "price = late stage; washed-out MVRV + SOPR capitulation + "
            "dull volume = accumulation territory. These are context "
            "indicators, not timers."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "market/fear-greed",
        "title": "Crypto fear & greed index — a sentiment tool",
        "topic": "market-cycles",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "The Crypto Fear & Greed Index (0-100) composites volatility, "
            "market momentum/volume, social media, surveys, and dominance "
            "into a sentiment score. Extreme fear (0-20) historically marks "
            "panic-selling bottoms; extreme greed (80-100) marks euphoric "
            "tops. As a tool it is contrarian and coarse: extreme greed "
            "with rising prices often means the crowd is all-in (fuel "
            "exhausted); extreme fear with crashing prices means the crowd "
            "has capitulated (potential opportunity). Limitations: it lags, "
            "it averages out divergent segments, and regime changes "
            "(institutional flows, ETF approval) can make it stay extreme "
            "longer than expected. Use it as one input alongside on-chain "
            "cost-basis data, funding rates, and volume — never as a "
            "standalone signal."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "market/macro-drivers",
        "title": "Macro drivers — rates, liquidity, and risk appetite",
        "topic": "market-cycles",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Crypto trades as a risk asset, so macro liquidity dominates "
            "long-term trends. Key drivers: 1) central-bank policy — rate "
            "hikes drain risk appetite (2022 bear), cuts and QE fuel it "
            "(2020-21 bull); watch the Fed, ECB, and BoJ, 2) global "
            "dollar liquidity — the M2/global liquidity proxy correlates "
            "with BTC trend, 3) stablecoin supply — rising USDT/USDC supply "
            "is 'dry powder' entering the market, 4) equity correlation — "
            "Nasdaq and BTC often move together in risk-on/off regimes, "
            "5) regulatory catalysts — ETF approvals, SEC actions, "
            "legislation change the structural demand. Framework: in "
            "easing/liquidity-expansion regimes, be risk-on with conviction; "
            "in tightening regimes, size down and hold cash. Macro beats "
            "TA in setting the tide; TA times the waves."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "market/technical-basics",
        "title": "Technical analysis basics — support, resistance, volume",
        "topic": "market-analysis",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Technical analysis (TA) studies price and volume to identify "
            "probable zones of supply and demand. Core concepts: support — "
            "a price level where buying historically absorbs selling; "
            "resistance — where selling caps rallies; breakouts above "
            "resistance on rising volume confirm strength, while "
            "breakdowns below support on volume confirm weakness. Volume "
            "is the confirmator: a breakout on shrinking volume is a trap. "
            "Common structures: trendlines, channels, head-and-shoulders "
            "(top = bearish, bottom = bullish), double tops/bottoms, and "
            "moving averages (50/200 — golden cross vs death cross). "
            "Crypto adds exchange-specific quirks: liquidation cascades "
            "make support levels sticky, and funding extremes create "
            "cascades. TA is probability, not certainty — always pair it "
            "with on-chain and fundamental context."
        ),
    },
    # ═══════════════════════════════════════════════════════════════
    # CROSS-CHAIN COMPARISONS
    # ═══════════════════════════════════════════════════════════════
    {
        "source": "crypto-expert",
        "rel_key": "crosschain/solana-vs-ethereum",
        "title": "Solana vs Ethereum — architecture and tradeoffs",
        "topic": "cross-chain",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Solana and Ethereum represent different design philosophies. "
            "Ethereum: a general-purpose L1 with a mature ecosystem, "
            "massive TVL, deep DeFi liquidity, and security via a large "
            "validator set; throughput is limited (~15-30 TPS on L1) so it "
            "relies on L2 rollups for scale; fees are high on L1 but "
            "cheap on L2s; finality is ~12-15 minutes (probabilistic). "
            "Solana: a single high-throughput L1 (~thousands of TPS) using "
            "parallel execution, Tower BFT consensus, and a ~400ms slot "
            "time; fees are fractions of a cent; finality is fast "
            "(optimistic, ~2-5 slots); tradeoffs are a more complex runtime, "
            "historical outage risk, and fewer battle-tested DeFi primitives "
            "relative to ETH. For users: ETH = deepest liquidity and "
            "institutional rails; SOL = speed, low fees, and a vibrant "
            "consumer/meme ecosystem. Cross-chain projects increasingly "
            "deploy on both."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "crosschain/l2-rollups",
        "title": "Layer-2 rollups — optimistic vs ZK, and sidechains",
        "topic": "cross-chain",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Layer-2s scale Ethereum by processing transactions off-chain "
            "and settling proofs on L1. Optimistic rollups (Arbitrum, "
            "Optimism, Base) assume transactions are valid and run a "
            "challenge window (~7 days) for fraud proofs — fast and "
            "compatible with existing tooling, but withdrawals are slow and "
            "security depends on at least one honest challenger. ZK rollups "
            "(zkSync, Starknet, Linea) post validity proofs (SNARKs/STARKs) "
            "— instant finality, no challenge window, but more complex "
            "proof generation and limited EVM compatibility (though zkEVM "
            "progress is narrowing this). Sidechains (Polygon PoS, Gnosis) "
            "are separate chains with their own consensus — cheaper and "
            "fast but inherit L1 security only through bridges, which are "
            "the classic hack target. When comparing L2s: throughput, "
            "finality time, withdrawal latency, bridge security, and "
            "ecosystem depth."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "crosschain/bridges",
        "title": "Cross-chain bridges — the security weak point",
        "topic": "cross-chain",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Bridges move assets and messages between chains and are the "
            "most exploited category in DeFi history (Wormhole, Ronin, "
            "Nomad, Harmony — billions lost). Bridge designs: 1) "
            "custodial/trusted — a multisig or federation holds assets "
            "(fast, but a single hack drains everything), 2) "
            "lock-and-mint — smart contracts lock native and mint "
            "wrapped (risk: contract exploits, validator compromise), 3) "
            "burn-and-mint, 4) liquidity networks — net-settled pools "
            "(fewer funds at risk). Risk factors: TVL concentration, "
            "permissionless validators vs trusted multisig, fraud/validity "
            "proofs, audit history, and insurance. Safest paths: canonical "
            "bridges from the L1 team, or intent-based and atomic-swap "
            "designs that minimize locked capital. When analyzing any "
            "wrapped token, verify the bridge backing and its security "
            "model — wrapped assets are only as safe as their bridge."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "crosschain/token-standards",
        "title": "Token standards — ERC-20/721/4626 vs SPL and Token-2022",
        "topic": "cross-chain",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Token standards define how assets behave on-chain. Ethereum "
            "standards: ERC-20 (fungible), ERC-721 (NFT), ERC-1155 (multi-"
            "token), ERC-4626 (tokenized vaults — yield vaults standardize "
            "deposit/redeem), ERC-4337 (account abstraction), ERC-2612 "
            "(permit — gasless approvals). Solana uses the SPL Token "
            "program: one program manages all fungible and NFT mints; "
            "Token-2022 adds extensions — transfer fees, interest-bearing "
            "tokens, confidential transfers, metadata — natively on-chain. "
            "Key differences: ERC-20 requires per-token contracts and "
            "approval flows; SPL uses PDAs and associated token accounts "
            "(1 account per wallet/mint). For analysis: check which "
            "standard a token uses (fee-on-transfer and rebasing behaviors "
            "differ), whether the mint is upgradeable/freezable (Token-2022 "
            "freeze authority), and how approvals/allowances work — "
            "infinite approvals on EVM are a top wallet-drain vector."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "crosschain/finality-fees",
        "title": "Finality and fees — comparing chain user experience",
        "topic": "cross-chain",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Chain user experience is defined by fees, speed, and "
            "finality. Ethereum L1: high fees in congestion, ~12s blocks, "
            "probabilistic finality (~12-15 min) — safe but expensive for "
            "small trades. L2s (Arbitrum/Base/OP): sub-cent to cent fees, "
            "near-instant confirmation, ~7-day withdrawal delay to L1 "
            "(optimistic) or minutes (ZK). Solana: fractions of a cent, "
            "400ms slots, fast optimistic finality — designed for retail "
            "and high-frequency use; priority fees via compute units. "
            "Other majors: Base (coinbase-backed, cheap, EVM), Polygon "
            "(sidechain/zkEVM), BNB Chain (fast, centralization tradeoff), "
            "Avalanche (subnets, fast finality), Cosmos zones (app-chains, "
            "IBC). When comparing: real fee at current congestion, "
            "confirmed-vs-final transaction time, withdrawal latency, "
            "downtime history, and ecosystem/tooling maturity."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "crosschain/dev-solidity-rust",
        "title": "Cross-chain development — Solidity vs Rust/Anchor",
        "topic": "cross-chain",
        "knowledge_type": "expert_guide",
        "collection": "coding_knowledge",
        "content": (
            "Writing for EVM vs Solana is a different mental model. "
            "Solidity (EVM): contract-centric — state lives in contract "
            "storage, functions are transactions, logic is synchronous; "
            "security focus is reentrancy, access control, and oracle "
            "manipulation; tooling: Hardhat/Foundry, OpenZeppelin. Rust "
            "with Anchor (Solana): account-centric — every instruction "
            "explicitly lists the accounts it touches, state lives in "
            "PDAs/accounts, and the runtime enforces ownership checks; "
            "security focus is account validation (signer, owner, PDA "
            "seeds), CPI re-entrancy, and compute budget; Anchor "
            "constraints automate much of the boilerplate. Key patterns "
            "for porting: replace 'msg.sender' with explicit signer "
            "accounts; replace storage writes with account mutations; "
            "expect explicit error handling everywhere. Porting logic is "
            "easy; porting the security model is the hard part."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "crosschain/ecosystem-landscape",
        "title": "Ecosystem landscape — where value concentrates",
        "topic": "cross-chain",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Cross-chain value concentrates in a few core ecosystems. "
            "Ethereum: the value and liquidity center — majority of DeFi "
            "TVL, institutional access (ETFs), mature stablecoin "
            "infrastructure, and the settlement layer for most L2s. "
            "Solana: the retail/performance center — consumer apps, meme "
            "economy, DePIN, fast settlement, increasingly institutional "
            "(tokenized RWA, payments). L2s (Arbitrum, Base, Optimism): "
            "Ethereum-scale execution with cheap fees — each builds its "
            "own app ecosystem while inheriting ETH liquidity. Other "
            "notables: BNB Chain (Asia retail), Polygon (zkEVM + "
            "enterprise), Avalanche (institutional subnets), Sui/Aptos "
            "(Move-based, fast), Cosmos/Polkadot (app-chains, "
            "interoperability). When a token lives on one chain, its "
            "ecosystem health, not just its own metrics, drives value — "
            "check TVL, active developers, and stablecoin flows for the "
            "host chain."
        ),
    },
    # ═══════════════════════════════════════════════════════════════
    # MEV AND SANDWICH ATTACKS
    # ═══════════════════════════════════════════════════════════════
    {
        "source": "crypto-expert",
        "rel_key": "mev/what-is-mev",
        "title": "MEV — maximal extractable value explained",
        "topic": "mev",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Maximal extractable value (MEV, formerly miner-extractable "
            "value) is the profit block producers (validators/sequencers) "
            "or bots can extract by reordering, inserting, or censoring "
            "transactions within a block. Sources: 1) arbitrage — buying "
            "cheap on one DEX and selling dear on another, 2) sandwich "
            "attacks — front-running a large trade with a buy and "
            "back-running it with a sell, 3) liquidations — racing to "
            "liquidate positions for the bonus, 4) JIT (just-in-time) "
            "liquidity — adding LP liquidity right before a big swap and "
            "removing it after. MEV is extracted by sophisticated bots "
            "paying priority fees; it inflates costs for ordinary traders "
            "(slippage) and can destabilize protocols. The MEV industry "
            "has professionalized into relays, builders, and private "
            "mempools. Understanding MEV is essential for evaluating DEX "
            "execution quality and protocol security."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "mev/sandwich-mechanics",
        "title": "Sandwich attack — exact mechanics",
        "topic": "mev",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "A sandwich attack front-runs a victim's swap and back-runs it "
            "for guaranteed profit. Mechanics on an AMM: 1) the bot "
            "watches the mempool for a large pending swap, 2) it submits "
            "a buy transaction that executes first (front-run), pushing "
            "price up, 3) the victim's swap executes at the worsened price "
            "(more slippage than expected), 4) the bot sells immediately "
            "after (back-run), capturing the price difference minus fees. "
            "The attack profits whenever the victim's trade size is large "
            "enough to move the pool. Detection signals: two transactions "
            "to the same pool sandwiching the victim's tx, from the same "
            "EOA, with the front-run buying and back-run selling the same "
            "token. Defenses for protocols: tighter slippage limits, "
            "TWAP oracles, private transactions, and MEV-aware routing "
            "(RFQ/intent systems). Users: set strict slippage and use "
            "private/MEV-protected RPCs."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "mev/frontrunning-backrunning",
        "title": "Front-running and back-running — order-based extraction",
        "topic": "mev",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Front-running exploits transaction ordering: an attacker sees "
            "a profitable pending transaction (in the mempool) and submits "
            "their own transaction first to capture value — buying ahead "
            "of a known large buy, or racing to liquidate before others. "
            "Back-running executes right after a transaction to profit "
            "from its effect — e.g. selling into a large buy's price "
            "impact. In permissionless systems, anyone can pay higher gas "
            "to reorder; on Solana, priority fees and runtime scheduling "
            "enable the same game. Classic vulnerable patterns: "
            "last-highest-bid auctions, first-come-first-served "
            "allocations, and any logic whose outcome depends on "
            "transaction order. Mitigations: commit-reveal schemes, "
            "batch/auction designs, oracles instead of on-chain "
            "sequencing, private mempools, and MEV auctions (PBS) that "
            "internalize extraction. Order dependence is a design smell — "
            "audit for it."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "mev/protection",
        "title": "MEV protection — private mempools, intents, RFQ",
        "topic": "mev",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "MEV protection routes trades away from public mempools where "
            "bots can front-run them. Mechanisms: 1) private/whitelisted "
            "mempools — Flashbots Protect, bloxroute, and RPC providers "
            "send transactions directly to builders/validators, hiding "
            "them from sandwich bots; 2) intent-based systems — users "
            "declare what they want (e.g. 'swap X for Y with max 1% "
            "slippage') and solvers compete to fill it (UniswapX, "
            "CoW Swap) — the solver bears execution risk, not the user; "
            "3) RFQ/request-for-quote — market makers quote a firm price "
            "and fill directly, eliminating pool slippage; 4) MEV-aware "
            "DEX routing — aggregators that account for sandwich risk "
            "when splitting orders. User guidance: use MEV-protected RPCs, "
            "set tight slippage, prefer intent/RFQ DEXs for large trades, "
            "and split very large orders. Protocol guidance: design for "
            "order-fairness and consider MEV internalization."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "mev/solana-mev",
        "title": "MEV on Solana — priority fees and the different game",
        "topic": "mev",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Solana's MEV landscape differs from Ethereum's. There is no "
            "public mempool in the same sense; transactions go to the "
            "leader's TPU (transaction processing unit) and leaders can "
            "reorder within a slot, with priority fees (compute-unit "
            "price) bidding for front-of-line placement. Sandwich attacks "
            "occur at the DEX level (Jupiter/Orca pools), usually via "
            "monitoring of pending txs through RPC subscriptions and "
            "Jito bundles. Jito (the Solana MEV client) runs an auction "
            "where bundles can include backrun instructions — much of "
            "Solana's MEV is now extracted via Jito tips rather than "
            "adversarial mempool races. For users: use Jito-protected "
            "RPCs or swap aggregators with built-in protection; for "
            "protocols: leverage the Jito bundling layer, use TWAP "
            "oracles over spot, and beware arbitrage opportunities "
            "baked into pool math. Solana MEV is younger and faster-"
            "moving than EVM MEV."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "mev/defensive-design",
        "title": "MEV-resistant protocol design patterns",
        "topic": "mev",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Protocols can design against MEV rather than fight it "
            "reactively. Patterns: 1) commit-reveal — hide intent until "
            "execution (randomness and sealed bids), 2) batch/auction "
            "settlement — process trades in batches at a uniform price "
            "(periodic batch auctions), 3) time-weighted pricing — TWAP "
            "oracles over spot to defeat instantaneous manipulation, "
            "4) fair ordering — commit-based sequencing that punishes "
            "reordering, 5) internalize MEV — run your own auction "
            "(PBS-style) and distribute profits to LPs/users, 6) "
            "minimum-delay buffers, 7) variable rate-limiting on "
            "governance-sensitive actions. Audit checklist: enumerate "
            "every state change that depends on transaction order or "
            "spot prices; ask 'can a bot profitably reorder, insert, or "
            "censor this?'; assume adversarial ordering in threat "
            "models. MEV resistance is a property you design in, not a "
            "patch you add later."
        ),
    },
    # ═══════════════════════════════════════════════════════════════
    # SMART CONTRACT SECURITY PATTERNS
    # ═══════════════════════════════════════════════════════════════
    {
        "source": "crypto-expert",
        "rel_key": "security/cei-pattern",
        "title": "Checks-Effects-Interactions — the core defensive pattern",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Checks-Effects-Interactions (CEI) is the foundational pattern "
            "against reentrancy. Order every function: 1) Checks — validate "
            "conditions (require/auth, balances, prices, deadlines), 2) "
            "Effects — update state first (balances, flags, mappings), 3) "
            "Interactions — only then make external calls (transfers, "
            "CPIs, callbacks). Because state is already updated before the "
            "external call, a reentrant call sees the new state and cannot "
            "double-withdraw. Example: in withdraw(), subtract the balance "
            "from storage BEFORE the transfer — a malicious recipient "
            "re-entering the function finds a zeroed balance. CEI also "
            "protects against cross-function reentrancy. Add explicit "
            "reentrancy guards (ReentrancyGuard/nonReentrant) for defense "
            "in depth, and beware read-only reentrancy where view "
            "functions report stale state to oracles/aggregators. On "
            "Solana, apply the same discipline to account mutations before "
            "CPIs."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/access-control",
        "title": "Access control patterns — roles, owners, and upgrades",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Access control limits who can call privileged functions. "
            "Patterns: Ownable (single owner), Roles (OpenZeppelin "
            "AccessControl — granular roles like MINTER_ROLE, PAUSER_ROLE, "
            "each with an admin), timelock (privileged actions execute "
            "only after a delay — users can exit before changes land), and "
            "multisig (M-of-N signers for admin keys). Rules: never use "
            "tx.origin for authorization (phishing vector); always use "
            "msg.sender / explicit signer accounts; grant the minimum "
            "permission needed; make admin changes two-step (set + "
            "confirm) so a typo doesn't brick the contract; document every "
            "privileged role in the threat model. Upgradeable contracts "
            "add proxy risk — the admin can change implementation, so the "
            "upgrade key IS the contract's security. Audit findings "
            "repeatedly show: missing onlyOwner, exposed internal "
            "functions, and single-step dangerous setters."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/oracle-manipulation",
        "title": "Oracle manipulation — why spot prices lie",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Oracle manipulation attacks feed a protocol a false price to "
            "profit from the difference. The classic vector: reading spot "
            "price from an AMM pool the attacker can manipulate — flash-"
            "loan a huge amount into the pool, move the spot price, trigger "
            "the victim protocol's dependent action (liquidation, mint, "
            "swap), then unwind the flash loan. Defenses: 1) TWAP — "
            "time-weighted average price over N blocks smooths out "
            "manipulation (Uniswap v2/v3 TWAP oracles), 2) Chainlink-style "
            "aggregated oracles from multiple independent sources with "
            "deviation thresholds, 3) circuit breakers — pause the "
            "dependent action when price moves beyond a band, 4) "
            "validation — require multiple oracles agree, 5) avoid "
            "oracle-read in the same transaction as the pool trade. "
            "Audit checklist: every price consumer must ask 'what if the "
            "price is wrong for one block?' and survive it. Depegged "
            "assets and illiquid pools are the highest-risk inputs."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/flash-loan-defenses",
        "title": "Flash loan attack defenses",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Flash loans let anyone borrow unlimited capital for one "
            "transaction, making capital-intensive attacks free to "
            "execute — oracle manipulation, governance takeovers, and "
            "arbitrage. You cannot stop flash loans, but you can "
            "neutralize the vulnerabilities they amplify: 1) never trust "
            "spot prices — use TWAP or external oracles, 2) time-lock "
            "governance changes so a flash-loan voter cannot swing an "
            "election (check voting power snapshot at block N, not live), "
            "3) bound rate changes and collateral factors with deviation "
            "limits, 4) validate invariants — e.g. protocol solvency after "
            "every action, 5) design functions to be manipulation-"
            "insensitive (the outcome must not depend on instantaneous "
            "pool state). Test with real flash-loan PoCs: simulate the "
            "largest possible borrow against every price-dependent "
            "function. A function that survives an unlimited flash loan is "
            "a function that survives most exploits."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/proxy-upgrade-safety",
        "title": "Proxy upgrade safety — storage layout and initializers",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Upgradeable proxies (UUPS, transparent, beacon) keep the "
            "contract address stable while the implementation changes — "
            "but introduce storage-layout hazards. Critical rules: 1) "
            "never reorder/remove existing state variables in the "
            "implementation (storage collision corrupts data), 2) append "
            "new variables at the end, 3) use storage gap patterns in "
            "inherited contracts to reserve slots, 4) initializers must be "
            "called once and protected (a public initialize() callable by "
            "anyone bricks the proxy), 5) verify the implementation "
            "contract's constructor doesn't write state (it runs in "
            "isolation, not in proxy context), 6) keep the upgrade "
            "function access-controlled and timelocked, 7) validate the "
            "new implementation address (not a selfdestructed contract, "
            "not a malicious one). Audit checklist: storage layout diff "
            "between versions, initializer guards, and upgrade "
            "authorization. Mango, Wormhole, and multiple bridge hacks "
            "trace to proxy or initializer flaws."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/token-standard-edge-cases",
        "title": "Token edge cases — fee-on-transfer, rebasing, malicious ERC20",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Not all ERC-20 tokens behave like the spec. Edge cases that "
            "break naive integrations: 1) fee-on-transfer tokens deduct a "
            "fee on every transfer, so received amounts ≠ transferred "
            "amounts — accounting breaks (use post-balance checks, "
            "SafeERC20's safeTransferFrom + balance verification), 2) "
            "rebasing tokens change holder balances over time (Ampleforth "
            "— treat balances as a snapshot), 3) tokens that return false "
            "instead of reverting (USDT historically) — check return "
            "values or use SafeERC20, 4) tokens with blacklists/freeze "
            "(USDC, USDT can freeze addresses), 5) tokens with hooks "
            "(ERC-777 reentrancy — the Uniswap v1/token incident), 6) "
            "upgradeable tokens whose behavior changes, 7) tokens that "
            "delegate to a proxy (be careful with address(this) logic). "
            "Integration checklist: test with real token contracts, not "
            "spec mocks; verify balances after every operation; reject "
            "unknown tokens at deposit; and document behavior "
            "assumptions. Token weirdness is a top audit finding."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/signature-replay",
        "title": "Signature and replay protection",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Signature-based features (permits, meta-transactions, "
            "cross-chain messages) fail without replay protection. Attack "
            "classes: 1) same-chain replay — the same signed message "
            "executed twice; prevent with nonces (per-address counter) and "
            "used-signature tracking, 2) cross-chain replay — a signature "
            "valid on Ethereum also valid on a fork/L2; bind the message "
            "to chain ID, contract address, and domain (EIP-712 domain "
            "separator), 3) signature malleability — ecrecover accepts "
            "non-canonical s-values; enforce low-s (OpenZeppelin ECDSA "
            "does this), 4) deadline/expiry missing — old signatures "
            "usable forever; add expiries, 5) wrong signer derivation — "
            "verify the recovered address is the authorized one, 6) "
            "phishing permits — users signing blind EIP-2612 messages; "
            "show clear UI. For Solana: verify off-chain signatures with "
            "ed25519 instruction and bind to program ID + message "
            "version. Audit checklist: nonce uniqueness, domain binding, "
            "s-value canonicalization, expiry enforcement."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/input-validation-dos",
        "title": "Input validation and denial-of-service resistance",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "contract_security",
        "content": (
            "Solidity/Solana programs fail security reviews on input "
            "validation and DoS resistance. Common bugs: 1) unbounded "
            "loops — attacker controls array length and griefs gas "
            "(pagination or bounded processing required), 2) arrays that "
            "grow forever — a push-only list eventually makes every "
            "interaction too expensive (use mappings + enumeration "
            "limits), 3) unchecked external calls — a failed call reverts "
            "the whole tx and bricks user flows (use try/catch or pull "
            "payments), 4) missing zero-address/zero-amount checks, 5) "
            "integer overflow in price/amount math (Solidity 0.8+ checks "
            "by default, but unchecked blocks and low-level ops bypass), "
            "6) division-by-zero or rounding to zero in fee math (favor "
            "the user on remainder), 7) denial via block gas limits — "
            "operations that exceed the block budget can never execute. "
            "Audit checklist: enumerate all array inputs, all external "
            "calls, all arithmetic, and ask 'what if this is empty, huge, "
            "or zero?'."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "security/wallet-dev-security",
        "title": "Wallet security patterns — keys, derivation, and UI",
        "topic": "smart-contract-security",
        "knowledge_type": "expert_guide",
        "collection": "coding_knowledge",
        "content": (
            "Building a wallet means owning key security. Core patterns: "
            "1) never store private keys in plaintext or logs — use secure "
            "enclaves (Secure Enclave, TPM), HSM, or MPC; encrypt at rest "
            "with a key derived from user passphrase (scrypt/Argon2 + "
            "AEAD), 2) seed phrase: BIP-39 mnemonic + BIP-32/44 "
            "derivation; warn users to back up offline; never send seeds "
            "over the network, 3) derivation: use hardened paths for "
            "account isolation; keep metadata out of the seed backup, "
            "4) approvals: request narrow approvals (specific amount/asset, "
            "not unlimited), surface every approval in UI, and offer "
            "revoke flows, 5) phishing resistance: validate URLs, "
            "origin-isolate the signing context, never auto-sign, "
            "6) transaction simulation before signing — show exactly what "
            "will happen (balance changes, approvals) and warn on "
            "suspicious patterns (transferFrom to unknown, infinite "
            "approval), 7) recovery: multisig, social recovery, or "
            "hardware fallback. The user's #1 loss vector is phishing and "
            "over-approval, not cryptography."
        ),
    },
    # ═══════════════════════════════════════════════════════════════
    # WALLET SECURITY BEST PRACTICES (user-facing)
    # ═══════════════════════════════════════════════════════════════
    {
        "source": "crypto-expert",
        "rel_key": "wallet/seed-phrase-safety",
        "title": "Seed phrase safety — the #1 self-custody rule",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Your seed phrase (12/24 words) IS your wallet — anyone with "
            "it controls every key derived from it. Golden rules: 1) never "
            "type your seed into any website, app, or 'verification' "
            "prompt — no legitimate service asks for it, 2) never store it "
            "digitally — no screenshots, no cloud notes, no email drafts, "
            "no chat messages, 3) write it on paper or stamp it on metal "
            "(fire/water-proof) and store copies in separate physical "
            "locations, 4) don't photograph it or share partial words "
            "(partial seeds can be brute-forced), 5) verify your backup "
            "works by restoring on a fresh device before relying on it, "
            "6) treat anyone claiming your 'wallet is compromised, send "
            "seed for verification' as a scammer — this is the most "
            "common phishing script. Hardware wallets keep the seed "
            "offline and only sign transactions — the gold standard for "
            "meaningful amounts."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/hardware-wallets",
        "title": "Hardware wallets — how they protect you (and don't)",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Hardware wallets (Ledger, Trezor, GridPlus, Keystone) store "
            "private keys in a secure element and sign transactions "
            "offline, so a compromised computer can't steal your keys. "
            "They defeat most malware and remote attacks. What they do NOT "
            "protect against: 1) phishing — if you approve a malicious "
            "transaction, the hardware wallet signs it (you must read the "
            "screen and verify the contract/amount), 2) supply-chain "
            "tampering — buy from official sources and verify "
            "authenticity, 3) physical theft + known PIN, 4) seed phrase "
            "compromise — the seed backup is still the attack surface, "
            "5) blind-signing — EIP-712 permits signed without review. "
            "Best practice: use a hardware wallet for large holdings, "
            "verify every signature on the device screen, keep the seed "
            "offline, enable PIN + passphrase, and use a separate hot "
            "wallet with small balances for daily activity."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/approvals-revokes",
        "title": "Token approvals — the silent drain vector",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "On EVM chains, approving a contract to spend your tokens "
            "grants it a standing allowance. Infinite/unlimited approvals "
            "('set approval for all') let a compromised or malicious "
            "contract drain your entire balance at any time. Prevention: "
            "1) approve only the exact amount needed for the transaction, "
            "2) approve only contracts you trust after checking the "
            "address (a lookalike phishing site often uses a lookalike "
            "contract), 3) for DeFi, some platforms require approvals — "
            "review the contract first, 4) periodically revoke unused "
            "approvals with tools like revoke.cash — revoke the old "
            "allowance to zero, 5) on Solana, the equivalent is the "
            "delegate/authority on your token accounts — check and revoke "
            "delegates via wallet or explorer. Signs of an attack: a "
            "transaction from your wallet you didn't make, or an approval "
            "to an address you don't recognize. Approvals are a top "
            "wallet-drain vector — audit them regularly."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/phishing-detection",
        "title": "Phishing detection — how to spot a fake",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Phishing is the leading cause of crypto theft. Red flags: 1) "
            "URL typosquats — 'uniswap-pad.com' vs 'uniswap.org'; verify "
            "the domain carefully (punycode and lookalike characters), 2) "
            "unsolicited airdrop/claim sites — 'claim your tokens' links "
            "in Discord/Twitter DM are scams that ask for approval or "
            "seed, 3) urgency pressure — 'your wallet will be frozen', "
            "'act now' — legitimate protocols never create panic windows, "
            "4) asking for seed phrase/private keys — never legitimate, "
            "5) fake customer support — support never DMs first, 6) "
            "malicious approvals — the site asks you to 'verify wallet' "
            "by approving a contract, 7) clipboard hijacking — copy/paste "
            "an address and verify both ends. Defenses: bookmark the real "
            "sites, use a hardware wallet (device screen shows the true "
            "recipient), keep small balances in hot wallets, and "
            "double-check every URL and contract address against the "
            "official source. When in doubt, don't connect."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/hot-vs-cold",
        "title": "Hot vs cold wallets — the right split",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Hot wallets (browser extensions, mobile apps, exchange "
            "accounts) are connected to the internet — convenient for "
            "daily trading, but exposed to malware and phishing. Cold "
            "wallets (hardware, paper, air-gapped) hold keys offline — "
            "secure for long-term storage but slower to use. Best "
            "practice is a tiered split: 1) a small hot wallet (amount you "
            "can afford to lose) for daily trading/mints, 2) a cold "
            "wallet for the bulk of holdings, used rarely and carefully, "
            "3) an exchange account only for amounts you're actively "
            "trading (and withdraw promptly — not your keys, not your "
            "coins), 4) for large treasuries, multisig (Gnosis Safe) or "
            "institutional custody with insurance. Never consolidate "
            "everything into one hot wallet. The right split depends on "
            "usage frequency, but the principle is universal: keep only "
            "operating cash online, store wealth offline."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/transaction-verification",
        "title": "Transaction verification — what to check before signing",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Before signing any wallet transaction, verify: 1) the "
            "recipient/contract address matches the one you intend (not "
            "the phishing lookalike) — cross-check on Etherscan/explorer, "
            "2) the function being called — a 'claim' that calls "
            "approve() with a huge amount is a drain, 3) the amount and "
            "token being spent or approved — watch for approvals to "
            "'setApprovalForAll' (NFTs) or unlimited ERC-20 allowances, "
            "4) the chain/network (a tx meant for Ethereum signed on a "
            "fork can be replayed), 5) the gas/priority fee is "
            "reasonable, 6) on hardware wallets, read the device screen — "
            "it shows the true data, not what the phishing UI shows. "
            "Simulate before signing when possible (wallet simulation, "
            "Tenderly). If a transaction's effect isn't fully clear, "
            "don't sign it — the irreversible nature of crypto means one "
            "careless signature can drain everything. Slow is safe."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/impersonation-scams",
        "title": "Impersonation and support scams",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Impersonation scams pose as official support, developers, or "
            "influencers. Variants: 1) fake support — a 'moderator' DMs "
            "you after you post an issue, asks you to 'verify' your "
            "wallet or share a recovery phrase, 2) fake developers — "
            "someone claims to be from a project's team and offers to "
            "'fix' your transaction, 3) fake influencers/celebs — "
            "verified-looking accounts promise giveaways ('send 1 ETH, "
            "get 2 back') — never real, 4) fake job offers asking for "
            "wallet 'testing', 5) cloned Discord/Telegram servers with "
            "official-looking bots that DM fake links. Defenses: official "
            "support never initiates DMs and never asks for keys/seeds; "
            "verify accounts through official channels (website footer, "
            "not search results); remember that giveaways requiring you "
            "to send crypto first are always scams; check account age and "
            "handle history; and use the project's official docs to find "
            "support, not DMs. If a 'support agent' asks for your seed "
            "or a test transaction, block them."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/multisig-recovery",
        "title": "Multisig and recovery — protecting large holdings",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "For treasuries and large holdings, single-key custody is a "
            "single point of failure. Multisig (M-of-N) requires M "
            "signatures from N owners — Gnosis Safe (EVM) and Squads "
            "(Solana) are the standards. A 2-of-3 setup survives one "
            "compromised key or one lost key; 3-of-5 is common for DAO "
            "treasuries. Combine multisig with: 1) hardware wallets on "
            "every signer key, 2) keys held by different people/entities "
            "in different locations, 3) a timelock on treasury "
            "transactions so owners can respond to a compromise, 4) "
            "transaction previewing for every signer. Recovery planning: "
            "document what happens if a signer loses their key or leaves "
            "the org (key rotation), back up owner keys to separate "
            "secure locations, and periodically test signing a zero-value "
            "transaction end-to-end. The tradeoff is convenience vs "
            "safety — for meaningful value, multisig + hardware + "
            "timelock is the professional standard."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/solana-wallet-security",
        "title": "Solana wallet security — accounts, delegates, and risk",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Solana wallets (Phantom, Solflare, Backpack) work differently "
            "from EVM wallets. Key security points: 1) private keys are "
            "ed25519 — same seed-phrase rules apply; 2) token accounts "
            "are separate from the main wallet account — every "
            "SPL token you hold has its own associated token account "
            "(ATA), 3) delegates: an SPL token account can have a delegate "
            "authority that can spend a specified allowance — the Solana "
            "equivalent of an EVM approval; check and revoke delegates "
            "(Phantom shows delegated tokens), 4) freeze authority on "
            "some mints (incl. Token-2022) can freeze your balance, 5) "
            "dApp phishing is the same risk — fake sites asking to "
            "connect, sign a message, or approve a delegate drain "
            "wallets, 6) message signing: only sign messages you "
            "understand (login messages are fine; 'verify wallet' that "
            "changes authority is not). Use hardware support (Ledger), "
            "keep daily funds in hot, and audit delegates regularly."
        ),
    },
    {
        "source": "crypto-expert",
        "rel_key": "wallet/email-phishing",
        "title": "Email and link-based crypto phishing",
        "topic": "wallet-security",
        "knowledge_type": "expert_guide",
        "collection": "education",
        "content": (
            "Email phishing targets crypto users with fake exchange "
            "notices, wallet 'alerts', and airdrop announcements. Red "
            "flags: 1) sender address that mimics a real domain with "
            "subtle changes (e.g. coinbase-support@secuer.com), 2) "
            "urgency — 'account suspended', 'withdrawal failed', 'verify "
            "within 24h', 3) embedded links that don't match the "
            "displayed URL (hover and check), 4) attachments — never open "
            "them, 5) requests for private keys, seeds, or 'verification' "
            "payments. Defenses: never click links in unexpected crypto "
            "emails — type the exchange/wallet URL directly or use "
            "bookmarks; enable 2FA (hardware key preferred) on "
            "exchanges; check the actual sender domain; use a separate "
            "email for exchanges; and remember exchanges never ask for "
            "your seed phrase or password reset codes via email. When "
            "unsure, contact support through the official site, never "
            "through the email's reply link."
        ),
    },
]


def docs_for_collection(collection: str) -> list[dict[str, Any]]:
    """Return the curated docs routed to *collection*."""
    return [d for d in EXPERT_DOCS if d.get("collection", "education") == collection]


def collection_counts() -> dict[str, int]:
    """Return {collection: number_of_docs} for the curated set."""
    counts: dict[str, int] = {}
    for d in EXPERT_DOCS:
        c = d.get("collection", "education")
        counts[c] = counts.get(c, 0) + 1
    return counts


__all__ = ["EXPERT_DOCS", "collection_counts", "docs_for_collection"]
