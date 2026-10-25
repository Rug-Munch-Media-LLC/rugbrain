"""Chunk 8 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
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
]
