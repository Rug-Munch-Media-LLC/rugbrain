"""Chunk 4 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
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
]
