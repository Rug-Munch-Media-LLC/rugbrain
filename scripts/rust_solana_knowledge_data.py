#!/usr/bin/env python3
"""Curated Rust / Solana coding knowledge for ``ingest_rust_solana_knowledge.py``.

Curated from the Solana documentation (docs.solana.com / solana-labs/solana),
the Anchor framework docs (coral-xyz/anchor) and the solana-security-txt best
practices (neodyme-labs/solana-security-txt). Covers: accounts model, PDAs,
CPIs / cross-program invocation, token program, associated token accounts,
rent, system program and BPF.

These docs guarantee the coding-knowledge collection answers Solana coding
questions even when live fetching is unavailable (the ingester merges this
curated set with cloned repo content).
"""

from __future__ import annotations

from typing import Any

RUST_SOLANA_DOCS: list[dict[str, Any]] = [
    {
        "source": "solana-docs",
        "rel_key": "core/accounts",
        "title": "Solana account model — data, lamports, owner, executable",
        "topic": "accounts",
        "knowledge_type": "language_docs",
        "content": (
            "Everything on Solana is an account: a data blob with an owner program, "
            "a balance in lamports (1 SOL = 1e9 lamports), an executable flag, a "
            "rent epoch and a unique 32-byte address. Accounts are the only storage "
            "primitive — programs store state in accounts they own. Key facts: "
            "only the owner program can modify an account's data (the system program "
            "owns system accounts); account size is fixed at creation (realloc is "
            "limited and requires enough lamports for rent); every program needs its "
            "own account with executable=true. When writing a program you list "
            "accounts explicitly in the instruction, each with a role (signer, "
            "writable, read-only) — missing or swapped accounts are the #1 Solana "
            "security bug (arbitrary account injection). Always validate that the "
            "passed account is the expected one (address check + owner check)."
        ),
    },
    {
        "source": "solana-docs",
        "rel_key": "core/rent",
        "title": "Solana rent and rent exemption",
        "topic": "rent",
        "knowledge_type": "language_docs",
        "content": (
            "Solana accounts pay rent based on bytes-stored times time, but any account "
            "holding >= 2 years of rent (the rent-exempt minimum) is exempt forever. "
            "In practice every account you create must be rent-exempt, which means "
            "funding it with a few hundredths of a SOL. The rent-exempt minimum is "
            "computed as account_size * lamports_per_byte_year * 2. Anchor does this "
            "automatically with rent-exempt account constraints. Tips: compute "
            "lamports with Rent::get() minimum_balance(data_len); large accounts cost "
            "more rent; you can close an account and reclaim its lamports by "
            "transferring the balance to the owner (closing accounts lets you "
            "recover rent, a common pattern for refunds)."
        ),
    },
    {
        "source": "solana-docs",
        "rel_key": "core/system-program",
        "title": "Solana system program — create_account, transfer, assign",
        "topic": "system-program",
        "knowledge_type": "language_docs",
        "content": (
            "The system program (11111111111111111111111111111111) is the owner of "
            "all native accounts and performs: create_account (allocate data + fund "
            "rent), transfer (move lamports between accounts), assign (change an "
            "account's owner program), allocate (resize data), create_account_with_seed. "
            "Every new program-owned account must first be created by the system "
            "program (or a CPI to it) and then initialized by your program. Example "
            "in Anchor: use #[account(init, payer = signer, space = 8 + N)] which "
            "CPIs to the system program. Security: verify that accounts you think "
            "are system-owned actually are (owner == System::ID), otherwise an "
            "attacker can pass a fake account."
        ),
    },
    {
        "source": "solana-docs",
        "rel_key": "core/pda",
        "title": "Solana PDAs — program derived addresses, seeds, bumps",
        "topic": "pda",
        "knowledge_type": "language_docs",
        "content": (
            "A Program Derived Address (PDA) is an address deterministically derived "
            "from seeds + a bump + the program id: PDA = find_program_address(seeds, "
            "program_id). PDAs have no private key; the owning program can 'sign' "
            "for them via invoke_signed (CPIs with PDA seeds). Derivation: "
            "Pubkey::find_program_address(&[seeds], program_id) returns (pda, bump) — "
            "the bump is a byte that makes the address fall off the ed25519 curve. "
            'Anchor: #[account(seeds = [b"name", seed_arg.as_ref()], bump)] declares '
            "a PDA with automatic derivation. Security best practices: "
            "1) validate EVERY seed against the instruction arguments (an attacker "
            "can point seeds anywhere), 2) use canonical bump only (bump == "
            "find_program_address(...).1) to avoid PDA duplication attacks, "
            "3) never let user-controlled seeds collide with authority seeds, "
            "4) verify the derived PDA equals the passed account key before trusting "
            "it (Anchor does this automatically)."
        ),
    },
    {
        "source": "solana-docs",
        "rel_key": "core/cpi",
        "title": "Solana CPIs — cross-program invocation, invoke, invoke_signed",
        "topic": "cpi",
        "knowledge_type": "language_docs",
        "content": (
            "A cross-program invocation (CPI) lets your program call another "
            "program's instruction. Use invoke() for normal calls and invoke_signed() "
            "when your program must sign for a PDA it owns: "
            'invoke_signed(&instruction, &account_infos, &[&[b"seed", &bump]]) — '
            "the PDA seeds prove the owning program authorized the call. CPIs are how "
            "programs compose (e.g. a DEX calls the token program to move funds). "
            "Security rules: only pass account_infos the callee needs (principle of "
            "least privilege); re-validate accounts after the CPI (the callee may "
            "have changed them); beware 'CPI reentrancy' — a called program can call "
            "back into yours (use a reentrancy guard or account constraints); "
            "check the callee's program id before invoking (program id confusion "
            "attacks pass a malicious program with the same interface)."
        ),
    },
    {
        "source": "solana-docs",
        "rel_key": "core/token-program",
        "title": "Solana token program — SPL Token, mints, token accounts",
        "topic": "token-program",
        "knowledge_type": "language_docs",
        "content": (
            "The SPL Token program (TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA) "
            "manages fungible tokens. Core accounts: a mint (token metadata + supply, "
            "owned by the token program) and token accounts (a holder's balance for a "
            "specific mint, with a mint authority and freeze authority). Instructions: "
            "InitializeMint, MintTo, Transfer, Burn, Approve, FreezeAccount, "
            "CloseAccount. A token account is uniquely identified by (owner, mint); "
            "the Associated Token Account (ATA) is the canonical derivation: "
            "ATA = PDA(mint, owner) with seeds [owner, TOKEN_PROGRAM_ID, mint]. "
            "Use spl_token::instruction or the anchor-spl crate for type-safe "
            "transfers. Security: validate token account owner (the token program "
            "owns token accounts), mint matches, and the authority is a signer; "
            "watch for Token-2022 (TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb) "
            "differences (extensions, transfer fees, CPI guards)."
        ),
    },
    {
        "source": "solana-docs",
        "rel_key": "core/ata",
        "title": "Solana associated token accounts (ATA)",
        "topic": "token-program",
        "knowledge_type": "language_docs",
        "content": (
            "An Associated Token Account (ATA) is a deterministic token account "
            "derived from a wallet and a mint: "
            "associated_token_address = PDA seeds [wallet, TOKEN_PROGRAM_ID, mint]. "
            "The ATA program (ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL) "
            "creates and manages them so each (owner, mint) has exactly one token "
            "account. Use get_associated_token_address(wallet, mint) or in Anchor "
            "spl_token::TokenAccount. ATAs make transfers safe: a user's tokens can't "
            "be sent to a wrong/nonexistent account. Security: always verify the "
            "derived ATA equals the passed account when you accept a token account "
            "argument, otherwise users could point you at an arbitrary token account."
        ),
    },
    {
        "source": "solana-docs",
        "rel_key": "core/bpf",
        "title": "Solana BPF / SBF execution model",
        "topic": "bpf",
        "knowledge_type": "language_docs",
        "content": (
            "Solana programs compile to Solana Bytecode Format (SBF, historically "
            "called BPF — a variant of eBPF). Programs are deterministic, "
            "single-threaded and run on the Solana Virtual Machine with a compute "
            "budget (~200k compute units per transaction, upgradeable). Everything "
            "must be constant time where security matters. Because programs are "
            "stateless between executions, all state lives in accounts. Heap is "
            "bounded (32KB by default); stack frames are limited; use bump "
            "allocators and avoid unbounded recursion. Logging via msg!() is limited. "
            "The compute budget means expensive loops can hit the CU limit — design "
            "for bounded iteration and batch processing."
        ),
    },
    {
        "source": "anchor-docs",
        "rel_key": "framework/overview",
        "title": "Anchor framework — account constraints, program derive, errors",
        "topic": "anchor",
        "knowledge_type": "framework_docs",
        "content": (
            "Anchor is a Rust framework for Solana programs that eliminates "
            "boilerplate and many account-handling bugs. A program is "
            "#[program] mod, handlers are functions, accounts are structs with "
            "#[derive(Accounts)] and constraint attributes:\n"
            "use anchor_lang::prelude::*;\n"
            "#[program] mod counter { use super::*; pub fn increment(ctx: "
            "Context<Increment>) -> Result<()> { ctx.accounts.counter.count += 1; "
            "Ok(()) } }\n"
            "#[derive(Accounts)] pub struct Increment<'info> { "
            '#[account(mut, seeds = [b"counter"], bump)] pub counter: '
            "Account<'info, Counter>, }\n"
            "#[account] pub struct Counter { pub count: u64 }\n"
            "Anchor auto-generates: IDL (JSON interface), client bindings, and "
            "discriminator-based account validation. Security: Anchor enforces "
            "constraints at runtime (mut, signer, has_one, seeds, constraint), "
            "which prevents many common account bugs — but you still must reason "
            "about authority checks, reentrancy and CPI safety."
        ),
    },
    {
        "source": "anchor-docs",
        "rel_key": "framework/constraints",
        "title": "Anchor account constraints — signer, mut, has_one, seeds, constraint",
        "topic": "anchor",
        "knowledge_type": "framework_docs",
        "content": (
            "Anchor constraints validate accounts before the handler runs:\n"
            "- #[account(mut)] — account is writable.\n"
            "- #[account(signer)] — account signed the transaction.\n"
            "- #[account(has_one = owner)] — a stored address equals another account "
            "(prevents substitution).\n"
            '- #[account(seeds = [b"pool", pool_owner.key().as_ref()], bump)] — '
            "derives a PDA and requires the account key to match.\n"
            "- #[account(constraint = amount > 0 @ MyError::ZeroAmount)] — arbitrary "
            "condition with custom error.\n"
            "- #[account(init, payer = signer, space = 8 + 32, seeds = [...], bump)] — "
            "create + fund a PDA account.\n"
            "Common mistakes: forgetting mut on a state account (Anchor then treats "
            "it read-only and the handler fails or writes vanish), missing signer on "
            "an authority (anyone can call), using .key() in seeds but not checking "
            "has_one (seed collision). Let Anchor's macros do the derivation and "
            "validation rather than hand-rolling."
        ),
    },
    {
        "source": "anchor-docs",
        "rel_key": "framework/errors",
        "title": "Anchor errors — error codes, require!, ErrorCode",
        "topic": "anchor",
        "knowledge_type": "framework_docs",
        "content": (
            "Anchor programs return typed errors: #[error_code] pub enum MyError { "
            '#[msg("amount must be positive")] ZeroAmount, #[msg("unauthorized")] '
            "Unauthorized }\n"
            "Then require!(amount > 0, MyError::ZeroAmount); or use the constraint "
            "@ MyError::ZeroAmount syntax. Anchor error codes are 4-byte "
            "discriminators starting at 0x1770 (6000); custom codes start at 6000. "
            "Returning clear errors is both UX and security: it makes failure "
            "reasons auditable and prevents silent misbehavior. Use "
            "anchor_lang::prelude::error! / err! for early returns."
        ),
    },
    {
        "source": "solana-security",
        "rel_key": "security/pda-security",
        "title": "PDA security best practices — canonical bumps, seed validation",
        "topic": "pda",
        "knowledge_type": "security_pattern",
        "content": (
            "PDA bugs are among the most exploited Solana vulnerabilities: "
            "1) NON-CANONICAL BUMP: accept any bump byte in the instruction and you "
            "can derive the same PDA with a different bump — two accounts appear to "
            "be the same PDA (PDA duplication). Always require bump == "
            "find_program_address(seeds).1 (Anchor's bump attribute does this). "
            "2) UNVALIDATED SEEDS: if seeds come from instruction args, an attacker "
            "controls the PDA — validate every seed field against stored state "
            "(has_one / constraint). 3) WRONG PROGRAM ID: pass program_id into "
            "find_program_address or the PDA belongs to a different program; Anchor "
            "ties seeds to the current program id. 4) Missing signer check: a PDA "
            "can only sign via invoke_signed with EXACTLY the same seeds+bump that "
            "derived it. Always verify the PDA account's owner is your program."
        ),
    },
    {
        "source": "solana-security",
        "rel_key": "security/account-validation",
        "title": "Solana account validation checklist",
        "topic": "accounts",
        "knowledge_type": "security_pattern",
        "content": (
            "Before using any account in a handler, validate: "
            "1) Owner — account.owner == expected program (prevents fake accounts "
            "owned by attacker programs). 2) Key — the address is the one you "
            "expect (equals a derived PDA or has_one stored address). 3) Signer — "
            "any authority that must approve has signed (AccountInfo::is_signer). "
            "4) Mutability — writable accounts are declared mut; never trust data "
            "from read-only accounts you didn't validate. 5) Type/identity — for "
            "token accounts check mint and owner fields. Anchor automates most of "
            "this via constraints, but raw CPI handlers (process_instruction with "
            "AccountInfo slices) must do it manually. In vanilla Solana use "
            "accounts::is_signer, check owner == expected_program, and "
            "Pubkey::find_program_address for PDAs."
        ),
    },
    {
        "source": "solana-security",
        "rel_key": "security/close-account",
        "title": "Closing accounts securely — avoid drained funds",
        "topic": "accounts",
        "knowledge_type": "security_pattern",
        "content": (
            "When a program closes an account (transfers its lamports to a "
            "recipient), the account becomes a zero-lamport shell that the system "
            "program will wipe. Classic bug: closing an account but leaving its "
            "data readable, then an attacker recreates it at the same address with "
            "their own data, and the program treats stale data as valid (close "
            "account race). Secure close pattern: 1) zero out the data before "
            "transferring lamports, 2) after close, never use the account again in "
            "the same transaction, 3) ensure the closing authority is validated, "
            "4) use Anchor's close = <target> constraint which zeroes the account "
            "and refunds rent to the target."
        ),
    },
    {
        "source": "solana-security",
        "rel_key": "security/reentrancy",
        "title": "Solana reentrancy and CPI safety",
        "topic": "cpi",
        "knowledge_type": "security_pattern",
        "content": (
            "Solana programs can be reentered: during a CPI, the callee can call "
            "back into the caller before the caller finishes. If the caller read "
            "state before the CPI and writes after, the reentrant call sees stale "
            "state. Mitigations: 1) write all state changes BEFORE any CPI, 2) use "
            "a reentrancy guard flag in a program account, 3) never trust account "
            "data across CPIs without revalidation, 4) restrict which programs can "
            "CPI into yours (check callee program id). Also note: a PDA owned by "
            "your program can only be signed for by your program — an attacker "
            "cannot forge invoke_signed. Audit every external program you CPI to."
        ),
    },
]

ANCHOR_EXAMPLE_DOCS: list[dict[str, Any]] = [
    {
        "source": "anchor-docs",
        "rel_key": "examples/transfer-sol",
        "title": "Anchor example — transfer SOL via system program CPI",
        "topic": "system-program",
        "knowledge_type": "code_example",
        "content": (
            "use anchor_lang::prelude::*;\n"
            "use anchor_lang::system_program;\n"
            "#[program] mod pay { use super::*; pub fn pay(ctx: Context<Pay>, "
            "amount: u64) -> Result<()> {\n"
            "    system_program::transfer(\n"
            "        CpiContext::new(\n"
            "            ctx.accounts.system_program.to_account_info(),\n"
            "            system_program::Transfer { from: "
            "ctx.accounts.from.to_account_info(), to: ctx.accounts.to.to_account_info() },\n"
            "        ),\n"
            "        amount,\n"
            "    )?;\n"
            "    Ok(()) } }\n"
            "#[derive(Accounts)] pub struct Pay<'info> {\n"
            "    #[account(mut)] pub from: Signer<'info>,\n"
            "    #[account(mut)] pub to: SystemAccount<'info>,\n"
            "    pub system_program: Program<'info, System>,\n"
            "}\n"
            "Signer<'info> guarantees from signed; SystemAccount guarantees the "
            "recipient is a system-owned account; the CPI moves lamports and "
            "requires the from account to be a signer."
        ),
    },
    {
        "source": "anchor-docs",
        "rel_key": "examples/spl-transfer",
        "title": "Anchor example — SPL token transfer with CPI",
        "topic": "token-program",
        "knowledge_type": "code_example",
        "content": (
            "use anchor_lang::prelude::*;\n"
            "use anchor_spl::token::{self, Token, TokenAccount, Transfer};\n"
            "#[program] mod tip { use super::*; pub fn tip(ctx: Context<Tip>, "
            "amount: u64) -> Result<()> {\n"
            "    token::transfer(\n"
            "        CpiContext::new(ctx.accounts.token_program.to_account_info(),\n"
            "            Transfer { from: ctx.accounts.from.to_account_info(), "
            "to: ctx.accounts.to.to_account_info(), "
            "authority: ctx.accounts.authority.to_account_info() }),\n"
            "        amount,\n"
            "    )?;\n"
            "    Ok(()) } }\n"
            "#[derive(Accounts)] pub struct Tip<'info> {\n"
            "    #[account(mut)] pub from: Account<'info, TokenAccount>,\n"
            "    #[account(mut)] pub to: Account<'info, TokenAccount>,\n"
            "    pub authority: Signer<'info>,\n"
            "    pub token_program: Program<'info, Token>,\n"
            "}\n"
            "anchor_spl validates the token program id and the token account types. "
            "The authority (owner of `from`) must sign. Always check mint equality "
            "between from/to when necessary."
        ),
    },
    {
        "source": "anchor-docs",
        "rel_key": "examples/close-account",
        "title": "Anchor example — safe close with rent refund",
        "topic": "accounts",
        "knowledge_type": "code_example",
        "content": (
            "#[derive(Accounts)] pub struct Close<'info> {\n"
            "    #[account(mut, close = receiver, has_one = owner)] pub data: "
            "Account<'info, UserData>,\n"
            "    pub owner: Signer<'info>,\n"
            "    #[account(mut)] pub receiver: SystemAccount<'info>,\n"
            "}\n"
            "The close = receiver constraint: Anchor transfers the account's "
            "lamports to receiver, zeroes the account data, and sets a CLOSED "
            "discriminator so the account cannot be used again. This refunds the "
            "rent to the user and is the canonical way to return funds. The "
            "has_one = owner check prevents anyone but the owner closing the "
            "account."
        ),
    },
]

ALL_RUST_SOLANA_DOCS: list[dict[str, Any]] = RUST_SOLANA_DOCS + ANCHOR_EXAMPLE_DOCS
