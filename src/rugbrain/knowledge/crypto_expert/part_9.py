"""Chunk 9 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
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
