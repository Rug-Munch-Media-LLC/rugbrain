"""Chunk 5 of ``SECURITY_PATTERNS`` (generated; literal data only).

Source: scripts/contract_knowledge_data.py
"""

ITEMS = [
    {
            "pattern_id": "evm:fee-on-transfer-token",
            "title": "Fee-on-transfer / rebasing token accounting mismatch",
            "ecosystem": "ethereum",
            "category": "business-logic",
            "swc": "SWC-109",
            "severity": "high",
            "vulnerable_code": (
                "function deposit(uint256 amount) external {\n"
                "    require(token.transferFrom(msg.sender, address(this), amount));\n"
                "    shares[msg.sender] += amount;  // assumes full amount received\n"
                "}"
            ),
            "fixed_code": (
                "// Measure the actual received balance before/after, or require\n"
                "// tokens with no fee; use transferFrom then balance-delta.\n"
                "uint256 before = token.balanceOf(address(this));\n"
                "token.transferFrom(msg.sender, address(this), amount);\n"
                "uint256 received = token.balanceOf(address(this)) - before;\n"
                "shares[msg.sender] += received;"
            ),
            "description": (
                "Fee-on-transfer tokens take a cut on every transfer. Protocols "
                "that credit the nominal amount overstate deposits; rebasing "
                "tokens change balances outside transfers. Always measure "
                "balance deltas."
            ),
            "detection_heuristics": [
                "transferFrom credited as full amount without balance-delta",
                "no fee-on-transfer token handling",
            ],
            "references": ["https://github.com/SunWeb3Sec/DeFiHackLabs"],
        },
    {
            "pattern_id": "evm:unlimited-approval-drainer",
            "title": "Unlimited approval / wallet drainer pattern",
            "ecosystem": "ethereum",
            "category": "phishing",
            "swc": "SWC-115",
            "severity": "critical",
            "vulnerable_code": (
                "function approve(address spender, uint256 amount) external {\n"
                "    // User approves a malicious contract for type(uint256).max;\n"
                "    // the contract then calls transferFrom to drain the wallet.\n"
                "}"
            ),
            "fixed_code": (
                "// Never approve contracts you don't trust; revoke stale\n"
                "// approvals; wallets should flag unlimited approvals.\n"
                "// Protocols should minimize approval scope and use permit\n"
                "// with explicit amounts."
            ),
            "description": (
                "The core of wallet-drainer phishing kits: victim is tricked into "
                "approving a contract for max allowance; the drainer contract "
                "calls transferFrom repeatedly to sweep all assets (Inferno "
                "Drainer, Pink Drainer)."
            ),
            "detection_heuristics": [
                "approve(spender, type(uint256).max) to a contract",
                "contracts that request approval then immediately transferFrom",
            ],
            "references": ["https://www.malwarebytes.com/blog/news/2023/01/pink-drainer"],
        },
    {
            "pattern_id": "sol:missing-signer-check",
            "title": "Missing signer check (requires_signer)",
            "ecosystem": "solana",
            "category": "access-control",
            "swc": "",
            "severity": "critical",
            "vulnerable_code": (
                "pub fn process(ctx: Context<Process>) -> Result<()> {\n"
                "    // Account passed as authority WITHOUT is_signer constraint\n"
                "    let authority = &ctx.accounts.authority;\n"
                "    // attacker supplies any account as 'authority'\n"
                "}"
            ),
            "fixed_code": (
                "#[derive(Accounts)]\n"
                "pub struct Process<'info> {\n"
                "    #[account(mut)]\n"
                "    pub authority: Signer<'info>,  // requires signature\n"
                "}"
            ),
            "description": (
                "In Solana, a transaction can reference any account; 'authority' "
                "means nothing unless the program checks the account actually "
                "signed. Missing is_signer/Signer constraints let an attacker "
                "pass an arbitrary account and bypass authorization."
            ),
            "detection_heuristics": [
                "account used for authority/owner without Signer<'info> or is_signer check",
                "AccountInfo checked only by key, not signer flag",
            ],
            "references": [
                "https://github.com/coral-xyz/anchor",
                "https://github.com/solana-labs/solana-program-library",
            ],
        },
    {
            "pattern_id": "sol:missing-owner-check",
            "title": "Missing owner (program) check on accounts",
            "ecosystem": "solana",
            "category": "input-validation",
            "swc": "",
            "severity": "critical",
            "vulnerable_code": (
                "let data = account.data.borrow();\n"
                "let unpacked = State::try_from_slice(&data)?;  // no owner check\n"
                "// any account (e.g. a user's arbitrary data account) is decoded"
            ),
            "fixed_code": (
                "// Anchor: owner is checked automatically for typed accounts.\n"
                "#[account]\n"
                "pub struct State { ... }\n"
                "// Or explicitly: require!(account.owner == program_id);"
            ),
            "description": (
                "Reading/decoding an account without verifying its owner lets an "
                "attacker pass a forged account with attacker-controlled data, "
                "enabling type confusion and privilege escalation."
            ),
            "detection_heuristics": [
                "AccountInfo data decoded without owner == program_id check",
                "Account<'info, T> without #[account] constraint (owner check)",
            ],
            "references": [
                "https://github.com/solana-labs/solana-program-library",
                "https://book.anchor-lang.com/",
            ],
        },
    {
            "pattern_id": "sol:pda-derivation-check",
            "title": "PDA derivation not verified (wrong seeds)",
            "ecosystem": "solana",
            "category": "input-validation",
            "swc": "",
            "severity": "critical",
            "vulnerable_code": (
                '// Expected: PDA derived from [b"vault", authority.key()]\n'
                "// Missing: find_program_address check -> attacker passes a\n"
                "// different 'vault' account that the program can never own."
            ),
            "fixed_code": (
                'let (expected_pda, bump) = Pubkey::find_program_address(&[b"vault", authority.key().as_ref()], program_id);\n'
                "require!(vault.key() == expected_pda, InvalidPda);\n"
                "require!(bump == vault_bump, InvalidPda);  // Anchor: seeds + bump constraint"
            ),
            "description": (
                "Programs must re-derive the PDA from fixed seeds and compare, and "
                "store/verify the bump. A missing derivation check lets an "
                "attacker route funds/state to an arbitrary account."
            ),
            "detection_heuristics": [
                "PDA account passed without seeds+bump constraint",
                "find_program_address not called before using a PDA account",
            ],
            "references": [
                "https://book.anchor-lang.com/",
                "https://github.com/solana-labs/solana-program-library",
            ],
        },
]
