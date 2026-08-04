#!/usr/bin/env python3
"""Curated Move / Cairo coding knowledge for ``ingest_alt_vm_knowledge.py``.

Curated from the Move language docs (move-language/move), the Move Book,
Aptos/Sui Move conventions, the Cairo book (starkware-libs/cairo) and
StarkNet contract guidance. Covers: resource model, abilities, modules,
scripts, access control and formal verification.

These docs guarantee the coding-knowledge collection answers Move/Cairo coding
questions even when live fetching is unavailable (the ingester merges this
curated set with cloned repo content).
"""

from __future__ import annotations

from typing import Any

MOVE_DOCS: list[dict[str, Any]] = [
    {
        "source": "move-docs",
        "rel_key": "language/resources",
        "title": "Move resource model — linear types, resources cannot be copied or dropped",
        "topic": "resource-model",
        "knowledge_type": "language_docs",
        "content": (
            "Move's core innovation is the resource model: values with the resource "
            "ability are linear — they can be moved but never copied or implicitly "
            "dropped. A resource exists in exactly one place at a time; moving it "
            "out of storage or destroying it requires explicit operations. This "
            "makes double-spend and accidental loss impossible at the language "
            "level: a coin is a resource, so it cannot be duplicated or destroyed "
            "without explicit code. Declared as: struct Coin has store { value: u64 } "
            "— store lets it live in global storage. Compare to Solidity where "
            "balances are just mapping values that any contract logic can corrupt; "
            "in Move the type system enforces asset safety. Resources are stored "
            "under an address in global storage: move_to<Coin>(&signer, coin) and "
            "move_from<Coin>(addr), or borrowed with borrow_global_mut."
        ),
    },
    {
        "source": "move-docs",
        "rel_key": "language/abilities",
        "title": "Move abilities — copy, drop, store, key",
        "topic": "resource-model",
        "knowledge_type": "language_docs",
        "content": (
            "Move has four abilities that control what you can do with a type: "
            "copy (values can be duplicated), drop (values can be discarded), "
            "store (values can be held in global storage), key (the type can be a "
            "storage key — stored at an address). A struct declares them: "
            "struct Account has key, store { balance: u64 }. A resource is a type "
            "WITHOUT copy and drop. Abilities compose: a type only has an ability "
            "if all its fields do. Solidity has no equivalent — anything can be "
            "copied and overwritten. Move's ability system is what powers safe "
            "token standards (Aptos coin, Sui object) and is a key differentiator "
            "for auditors: check which abilities a struct declares before trusting "
            "it with assets."
        ),
    },
    {
        "source": "move-docs",
        "rel_key": "language/modules",
        "title": "Move modules — code organization, friends, entry functions",
        "topic": "modules",
        "knowledge_type": "language_docs",
        "content": (
            "Move code is organized into modules (like Solidity contracts but "
            "decoupled from addresses): module 0x1::coin { ... }. A module is "
            "published once and its functions are called by address::module::fn. "
            "Entry functions (entry fun) are the public transaction interface — "
            "like Solidity external functions. Visibility: public, public(friend), "
            "public(script), private (module-internal). friend modules can call "
            "friend functions — a mechanism for privileged cooperation (Aptos "
            "uses it for framework modules). Structs in a module can only be "
            "constructed/destructed inside that module (the module owns its "
            "types), which is the backbone of access control in Move — only the "
            "coin module can mint or burn Coin."
        ),
    },
    {
        "source": "move-docs",
        "rel_key": "language/scripts",
        "title": "Move scripts — transaction entrypoints",
        "topic": "scripts",
        "knowledge_type": "language_docs",
        "content": (
            "A Move script is a transaction-level entrypoint (main) that can call "
            "module functions. Scripts cannot define structs and cannot store "
            "resources — they orchestrate calls. Example: "
            "script { use 0x1::coin; fun main(account: &signer, to: address, "
            "amount: u64) { coin::transfer(account, to, amount); } } "
            "Scripts are ephemeral (not published). For most dApps you'll publish "
            "a module with entry functions instead of shipping scripts. On Sui "
            "the script concept is replaced by entry functions with TxContext."
        ),
    },
    {
        "source": "move-docs",
        "rel_key": "language/access-control",
        "title": "Move access control — signers, module-owned types, capabilities",
        "topic": "access-control",
        "knowledge_type": "security_pattern",
        "content": (
            "Access control in Move differs from Solidity: there is no msg.sender. "
            "Instead: 1) the &signer argument proves the caller controls that "
            "address, 2) module-owned types (structs constructible only inside the "
            "module) gate privileged operations, 3) capability patterns give "
            "delegated authority. Example admin check: fun only_admin(admin: "
            "&signer) acquires AdminCap { assert!(exists<AdminCap>("
            "signer::address_of(admin)), E_NOT_ADMIN ); }. On Aptos, "
            "signer::address_of + account::is_framework_address or a capability "
            "resource. Never trust arbitrary &signer without checking what it "
            "signs for; assert on the exact address or a stored capability. "
            "Sui uses object-based ownership: only the object owner can call "
            "entry functions taking &mut that object."
        ),
    },
    {
        "source": "move-docs",
        "rel_key": "language/formal-verification",
        "title": "Move formal verification — Move Prover, invariants, specs",
        "topic": "formal-verification",
        "knowledge_type": "language_docs",
        "content": (
            "Move ships with a built-in formal verification tool, the Move Prover "
            "(MVP): you annotate functions with preconditions (requires), "
            "postconditions (ensures), and global invariants, and the prover "
            "mathematically checks them: "
            "spec transfer { requires balance_of(from) >= amount; ensures "
            "balance_of(from) == old(balance_of(from)) - amount; } "
            "The Prover catches arithmetic overflow, out-of-bounds, resource "
            "leaks and invariant violations before deployment. Because Move is a "
            "restricted, deterministic language, verification is tractable — "
            "Solidity cannot be verified this way without external tools "
            "(Foundry, Certora). For audit teams, Move programs with prover specs "
            "are dramatically easier to reason about than EVM bytecode."
        ),
    },
    {
        "source": "move-docs",
        "rel_key": "comparison/move-vs-solidity",
        "title": "Move vs Solidity — resource model comparison",
        "topic": "resource-model",
        "knowledge_type": "comparison",
        "content": (
            "Move vs Solidity — the resource model difference: "
            "Solidity: state is a key-value storage mapping; balances are ordinary "
            "numbers; any contract code can add/subtract them; correctness depends "
            "on discipline (CEI, reentrancy guards, checked math). Assets are "
            "represented by accounting entries (ERC20 balanceOf). "
            "Move: assets are first-class resources with linear semantics — a coin "
            "cannot be copied, dropped, or double-spent because the type system "
            "forbids it. Storage is per-address global state (move_to/move_from); "
            "reentrancy is largely eliminated because resources move atomically "
            "and there are no external calls in the EVM sense (no callback into "
            "arbitrary code mid-transaction). Solidity is flexible but footgun-"
            "heavy; Move is restrictive but safer by construction. Cairo (StarkNet) "
            "sits in between: it has immutable state and a Rust-like model with "
            "explicit storage vars, but reentrancy protection is the developer's "
            "job (the framework provides non_reentrant)."
        ),
    },
]

CAIRO_DOCS: list[dict[str, Any]] = [
    {
        "source": "cairo-docs",
        "rel_key": "language/overview",
        "title": "Cairo / StarkNet contracts — overview",
        "topic": "cairo",
        "knowledge_type": "language_docs",
        "content": (
            "Cairo is the language of StarkNet (Starknet), a validity-rollup with "
            "STARK proofs. Cairo code compiles to Cairo assembly (Sierra for "
            "Starknet) and every transaction is proven. Contracts use the "
            "#[starknet::contract] attribute and #[storage] struct for state: "
            "#[starknet::contract] mod Counter { #[storage] struct Storage { "
            "count: u64 } #[event] #[derive(Drop, starknet::Event)] struct "
            "Incremented { value: u64 } #[constructor] fn constructor(ref self: "
            "ContractState) {} #[external(v0)] fn increment(ref self: "
            "ContractState) { self.count.write(self.count.read() + 1); "
            "self.emit(Incremented { value: self.count.read() }); } } "
            "State is read with self.x.read() and written with self.x.write(). "
            "The ContractState passed as ref self is the storage handle. "
            "Starknet contracts are class-based: a class is declared then "
            "instances are deployed, enabling upgrades via class replacement."
        ),
    },
    {
        "source": "cairo-docs",
        "rel_key": "language/access-control",
        "title": "Cairo access control — caller_address, Ownable pattern",
        "topic": "access-control",
        "knowledge_type": "security_pattern",
        "content": (
            "In Cairo you read the caller with get_caller_address() from "
            "starknet::get_caller_address (the counterpart to msg.sender). "
            "OpenZeppelin-Cairo provides Ownable: use openzeppelin::access::"
            "ownable::Ownable; with #[derive(starknet::Storage)] and "
            "only_owner() assertions. Pattern: "
            "fn only_owner(self: @ContractState) { let owner: ContractAddress = "
            "self.owner.read(); assert(owner == starknet::get_caller_address(), "
            "'caller is not owner'); } "
            "Security: NEVER use get_tx_info().unstable_signer for authorization "
            "(it's the L1 signer and is unstable); always compare against "
            "get_caller_address. Access control in Cairo mirrors Solidity: "
            "check caller, validate inputs, update state before external calls, "
            "and use the #[non_reentrant] attribute on mutating entrypoints."
        ),
    },
    {
        "source": "cairo-docs",
        "rel_key": "language/reentrancy",
        "title": "Cairo reentrancy protection — non_reentrant, components",
        "topic": "reentrancy",
        "knowledge_type": "security_pattern",
        "content": (
            "Starknet does not automatically prevent reentrancy. The "
            "openzeppelin::security::reentrancyguard::ReentrancyGuard component "
            "provides #[non_reentrant] on entrypoints: it stores a locked flag in "
            "the component's storage slot and reverts if already locked. "
            "Example: #[external(v0)] #[non_reentrant] fn withdraw(ref self: "
            "ContractState, amount: u256) { ... }. Cairo 1+ storage is immutable "
            "within a transaction until written, and calls to other contracts "
            "happen via library_call / call_contract — a called contract can "
            "reenter yours, so guard every entrypoint that moves value or makes "
            "external calls. Also validate return values from call_contract "
            "(it returns felt252-based data you must decode)."
        ),
    },
    {
        "source": "cairo-docs",
        "rel_key": "language/formal-verification",
        "title": "Cairo formal verification — native proof system",
        "topic": "formal-verification",
        "knowledge_type": "language_docs",
        "content": (
            "Cairo's defining property is that every program execution produces a "
            "STARK proof that is verified on Ethereum. That proves execution "
            "integrity (the code ran as written) but NOT business-logic "
            "correctness — a contract can execute exactly as written and still "
            "have exploitable logic. For semantic verification Starknet offers "
            "Cairo Formal Verification (via the smt checker in starknet-foundry "
            "scarb build, or the felt Cairo prover) and integration with "
            "certora/slither for Cairo. Best practice: combine the intrinsic "
            "execution proof with: 1) #[test] unit tests, 2) property tests with "
            "assertions, 3) formal specs for critical invariants (e.g. "
            "total_supply == sum of balances). Treat the STARK proof as "
            "execution integrity, not a security guarantee."
        ),
    },
    {
        "source": "cairo-docs",
        "rel_key": "comparison/cairo-vs-solidity",
        "title": "Cairo vs Solidity — state, fees, proving",
        "topic": "cairo",
        "knowledge_type": "comparison",
        "content": (
            "Cairo vs Solidity: both use account-based models with msg.sender "
            "equivalents (get_caller_address), but Cairo state is declared "
            "explicitly in #[storage] structs with read/write accessors, and "
            "transactions are proven off-chain by a prover (execution integrity "
            "on L1). Cairo is memory-safe (no raw pointers, felts are 252-bit), "
            "which eliminates whole bug classes (integer overflow still exists — "
            "use u256 checked ops from starknet::uint256). Cairo has components "
            "(reusable modules like Ownable, ReentrancyGuard) instead of Solidity "
            "libraries/inheritance. Deployment is class-based; upgrades replace "
            "the class hash. Gas (fees) is computed from Cairo steps and "
            "sierra gas — measure with starknet-foundry's snforge. Overall Cairo "
            "is more verbose but stricter than Solidity; Move is the strictest of "
            "the three (linear resources)."
        ),
    },
]

ALL_ALT_VM_DOCS: list[dict[str, Any]] = MOVE_DOCS + CAIRO_DOCS
