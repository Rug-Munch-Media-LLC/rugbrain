"""Chunk 2 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
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
]
