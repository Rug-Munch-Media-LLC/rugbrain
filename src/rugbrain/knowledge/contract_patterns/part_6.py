"""Chunk 6 of ``SECURITY_PATTERNS`` (generated; literal data only).

Source: scripts/contract_knowledge_data.py
"""

ITEMS = [
    {
            "pattern_id": "sol:cpi-signer-seeds",
            "title": "CPI without signer seeds (privilege escalation)",
            "ecosystem": "solana",
            "category": "access-control",
            "swc": "",
            "severity": "critical",
            "vulnerable_code": (
                "// Program tries to invoke a CPI that requires its PDA to sign,\n"
                "// but passes no signer_seeds -> attack surface if seeds are\n"
                "// attacker-influenced, or the CPI fails/reverts.\n"
                "invoke(&ix, &accounts)?;"
            ),
            "fixed_code": (
                'invoke_signed(&ix, &accounts, &[&[b"vault", authority.key().as_ref(), &[bump]]])?;\n'
                "// signer seeds must exactly match the PDA's canonical derivation"
            ),
            "description": (
                "CPIs that require program-derived signatures must pass "
                "signer_seeds matching the canonical PDA derivation. Wrong/missing "
                "seeds fail the CPI; seed confusion (using user-controlled bytes) "
                "can sign the wrong PDA."
            ),
            "detection_heuristics": [
                "invoke_signed missing or with non-canonical seeds",
                "CPI authority account not constrained to a PDA",
            ],
            "references": [
                "https://docs.solana.com/developing/programming-model/calling-between-programs"
            ],
        },
    {
            "pattern_id": "sol:close-account-reinit",
            "title": "Closed account reuse / missing close validation",
            "ecosystem": "solana",
            "category": "input-validation",
            "swc": "",
            "severity": "high",
            "vulnerable_code": (
                "// Closing an account without zeroing its data (or leaving a\n"
                "// lamports refund trap) allows reinitialization with stale state\n"
                "// or draining the rent-exempt lamports."
            ),
            "fixed_code": (
                "// Anchor: #[account(mut, close = destination)] zeroes lamports and\n"
                "// reallocates data to zero on close; verify the account is\n"
                "// already initialized before use."
            ),
            "description": (
                "Solana accounts can be closed (lamports moved, data left behind). "
                "If a program reuses a closed account without reinitializing or "
                "validating state, an attacker can reinitialize it with forged "
                "data — or harvest rent lamports if the close is not atomic."
            ),
            "detection_heuristics": [
                "account close without zeroing data or draining lamports",
                "no initialized/discriminator check before using account data",
            ],
            "references": [
                "https://book.anchor-lang.com/",
                "https://github.com/solana-labs/solana-program-library",
            ],
        },
    {
            "pattern_id": "sol:token-account-validation",
            "title": "Token account validation (wrong mint / wrong authority)",
            "ecosystem": "solana",
            "category": "input-validation",
            "swc": "",
            "severity": "critical",
            "vulnerable_code": (
                "// Source/token accounts passed without mint/authority constraints.\n"
                "// An attacker supplies a token account for a different mint or\n"
                "// owned by a different authority -> cross-mint confusion.\n"
                "let source = &ctx.accounts.source;"
            ),
            "fixed_code": (
                "// Anchor: #[account(mint::token = mint, token::authority = authority)]\n"
                "// or manually: source.mint == mint.key() && source.owner == authority.key()\n"
                "require!(source.mint == mint.key(), WrongMint);\n"
                "require!(source.owner == authority.key(), WrongAuthority);"
            ),
            "description": (
                "Token accounts carry mint and owner fields. Programs that fail to "
                "verify them accept the wrong mint (cross-mint accounting bugs) or "
                "move tokens the caller doesn't own."
            ),
            "detection_heuristics": [
                "token transfer CPI without mint/owner/authority checks",
                "destination/source accounts not constrained to the expected mint",
            ],
            "references": [
                "https://github.com/solana-labs/solana-program-library/tree/main/token/program"
            ],
        },
    {
            "pattern_id": "sol:checked-arithmetic",
            "title": "Unchecked arithmetic in Rust (overflow)",
            "ecosystem": "solana",
            "category": "integer-overflow",
            "swc": "",
            "severity": "high",
            "vulnerable_code": (
                "let new_balance = balance + amount;  // wraps in release builds (no overflow check)"
            ),
            "fixed_code": (
                "let new_balance = balance.checked_add(amount).ok_or(ErrorCode::Overflow)?;\n"
                "// or use Anchor's checked math helpers / overflow-checks = true"
            ),
            "description": (
                "Rust arithmetic wraps silently in release builds unless "
                "overflow-checks=true. Token/accounting math must use checked_add/"
                "checked_mul/checked_sub (Anchor requires checked math via "
                "Errors)."
            ),
            "detection_heuristics": [
                "bare + - * on u64/i64 amounts",
                "Cargo.toml without overflow-checks = true (or Anchor which enables it)",
            ],
            "references": [
                "https://book.anchor-lang.com/",
                "https://github.com/solana-labs/solana-program-library",
            ],
        },
    {
            "pattern_id": "sol:cross-program-reentrancy",
            "title": "Cross-program reentrancy via CPI",
            "ecosystem": "solana",
            "category": "reentrancy",
            "swc": "",
            "severity": "high",
            "vulnerable_code": (
                "// Program calls another program (CPI) while holding mutable\n"
                "// accounts; the callee can CPI back into this program and\n"
                "// observe/alter the same accounts mid-transaction.\n"
                "invoke(&token_ix, &token_accounts)?;"
            ),
            "fixed_code": (
                "// Validate all account state BEFORE the CPI, treat CPIs as\n"
                "// untrusted boundaries, and re-validate after; avoid holding\n"
                "// writable accounts across CPIs where possible."
            ),
            "description": (
                "Solana transactions are atomic, so a program can be re-entered "
                "via a CPI from a callee that calls back. Programs must complete "
                "state validation before external CPIs and not rely on "
                "mid-transaction invariants across CPIs."
            ),
            "detection_heuristics": [
                "mutable account held across a CPI",
                "CPI target not constrained to known programs",
            ],
            "references": [
                "https://docs.solana.com/developing/programming-model/calling-between-programs"
            ],
        },
]
