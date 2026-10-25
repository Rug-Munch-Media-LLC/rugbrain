"""Chunk 7 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
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
]
