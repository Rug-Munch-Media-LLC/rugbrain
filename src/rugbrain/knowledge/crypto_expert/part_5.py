"""Chunk 5 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
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
]
