"""Chunk 1 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
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
]
