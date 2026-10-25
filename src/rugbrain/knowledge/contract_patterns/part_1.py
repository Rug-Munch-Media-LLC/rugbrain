"""Chunk 1 of ``AUDIT_FINDINGS`` (generated; literal data only).

Source: scripts/contract_knowledge_data.py
"""

ITEMS = [
    {
            "finding_id": "audit:euler-2023",
            "title": "Euler Finance — donate-to-self + accounting flaw ($197M)",
            "ecosystem": "ethereum",
            "category": "business-logic",
            "severity": "critical",
            "pattern_class": "share-price manipulation via direct token donation",
            "description": (
                "March 2023: Euler's liquidate function allowed a 'donate-to-self' "
                "attack that inflated a sub-account's collateral value beyond its "
                "true balance, letting the attacker borrow against fake collateral "
                "and drain ~$197M. Root causes: spot-price reliance and accounting "
                "that could be skewed by direct token transfers."
            ),
            "detection_heuristics": [
                "value computed from token.balanceOf(this) * shares",
                "no protection against direct donation inflating share price",
                "liquidation/collateral math using manipulable spot value",
            ],
            "remediation": (
                "Use internal balance accounting, virtual shares / minimum "
                "liquidity, TWAP or manipulation-resistant pricing, and invariant "
                "checks that hold under arbitrary token balances."
            ),
            "reference_url": "https://www.euler.finance/blog/euler-exploit-incident",
            "amount_lost_usd": 197_000_000,
        },
    {
            "finding_id": "audit:mango-2022",
            "title": "Mango Markets — oracle manipulation ($114M)",
            "ecosystem": "solana",
            "category": "oracle-manipulation",
            "severity": "critical",
            "pattern_class": "price feed manipulation enabling undercollateralized borrows",
            "description": (
                "October 2022: an attacker used a single large order to move "
                "MNGO's oracle price, then borrowed against massively inflated "
                "collateral and drained ~$114M from Mango Markets (Solana). "
                "Oracle liveness/accuracy checks and per-user borrow caps were "
                "insufficient."
            ),
            "detection_heuristics": [
                "oracle price can be moved by a single large trade/order",
                "no staleness or deviation bounds on price updates",
            ],
            "remediation": (
                "Multiple oracle sources with deviation bounds, TWAP feeds, "
                "borrow caps, and liquidation safeguards independent of a single "
                "market."
            ),
            "reference_url": "https://rekt.news/mango-markets-rekt/",
            "amount_lost_usd": 114_000_000,
        },
    {
            "finding_id": "audit:wormhole-2022",
            "title": "Wormhole bridge — signature verification flaw ($326M)",
            "ecosystem": "ethereum",
            "category": "signature-malleability",
            "severity": "critical",
            "pattern_class": "validator signature verification bypass",
            "description": (
                "February 2022: the Wormhole Solana bridge let an attacker forge "
                "a valid 'consensus' message, minting 120k wETH (~$326M) on "
                "Solana. The guardian signature verification had a flaw that "
                "bypassed the validator set check."
            ),
            "detection_heuristics": [
                "signature aggregation/verification not strictly checking the full validator set",
                "message authenticity based on a single trusted signer or weak check",
            ],
            "remediation": (
                "Strictly verify all required signatures against the canonical "
                "validator set; test negative cases (empty set, repeated keys, "
                "malleated signatures)."
            ),
            "reference_url": "https://rekt.news/wormhole-rekt/",
            "amount_lost_usd": 326_000_000,
        },
    {
            "finding_id": "audit:bnb-bridge-2022",
            "title": "BNB Chain bridge — proof verification bypass ($570M)",
            "ecosystem": "ethereum",
            "category": "input-validation",
            "severity": "critical",
            "pattern_class": "cross-chain proof verification bypass",
            "description": (
                "October 2022: a flaw in the BSC Token Hub bridge proof "
                "verification let an attacker forge withdrawal messages and mint "
                "~1M BNB (~$570M). A low-level message-verification bug allowed "
                "spoofing the relayer proof."
            ),
            "detection_heuristics": [
                "cross-chain message verification missing hash/merkle checks",
                "relayer/signer list manipulable or not validated",
            ],
            "remediation": (
                "Formally verify cross-chain message verification; require "
                "multiple independent proofs; bound mint amounts."
            ),
            "reference_url": "https://rekt.news/bnb-chain-rekt/",
            "amount_lost_usd": 570_000_000,
        },
    {
            "finding_id": "audit:cream-2021",
            "title": "Cream Finance — flash-loan reentrancy ($130M)",
            "ecosystem": "ethereum",
            "category": "reentrancy",
            "severity": "critical",
            "pattern_class": "flash-loan amplified reentrancy in token strategy",
            "description": (
                "October 2021: Cream's AMP/CREAM token strategy was drained via a "
                "flash-loan funded reentrancy on the deposit/redeem path. The "
                "strategy contract allowed recursive redeem with stale accounting."
            ),
            "detection_heuristics": [
                "redeem/withdraw path calling external contracts before internal accounting settles",
                "no reentrancy guard on strategy/deposit functions",
            ],
            "remediation": "Reentrancy guards on all value-moving functions, CEI ordering, and strategy-level isolation.",
            "reference_url": "https://rekt.news/cream-rekt2/",
            "amount_lost_usd": 130_000_000,
        },
    {
            "finding_id": "audit:dao-2016",
            "title": "The DAO — reentrancy ($60M)",
            "ecosystem": "ethereum",
            "category": "reentrancy",
            "severity": "critical",
            "pattern_class": "split function reentrancy (the original DAO hack)",
            "description": (
                "June 2016: the first major DeFi exploit. The DAO's split "
                "function performed an external call before updating balances, "
                "allowing recursive splits that drained ~3.6M ETH (~$60M). Led "
                "to the Ethereum hard fork."
            ),
            "detection_heuristics": ["external call before state update in withdraw/split paths"],
            "remediation": "CEI pattern, reentrancy guards, and limiting external calls in critical paths.",
            "reference_url": "https://www.coindesk.com/learn/the-dao-heist-etherum-classic-vs-ethereum/",
            "amount_lost_usd": 60_000_000,
        },
    {
            "finding_id": "audit:parity-2017",
            "title": "Parity multi-sig — delegatecall selfdestruct ($280M frozen)",
            "ecosystem": "ethereum",
            "category": "access-control",
            "severity": "critical",
            "pattern_class": "delegatecall to a library containing selfdestruct",
            "description": (
                "November 2017: an attacker triggered the initWallet function of "
                "the Parity wallet library via delegatecall, called "
                "kill(parity wallet library), selfdestructing the shared library "
                "and freezing ~$280M in multi-sig wallets forever."
            ),
            "detection_heuristics": [
                "delegatecall target includes selfdestruct/SELFDESTRUCT",
                "init/upgrade functions reachable by non-owners",
            ],
            "remediation": "Never include selfdestruct in shared libraries; gate delegatecall strictly; use separated data/execution patterns.",
            "reference_url": "https://www.parity.io/blog/what-happened",
            "amount_lost_usd": 280_000_000,
        },
]
