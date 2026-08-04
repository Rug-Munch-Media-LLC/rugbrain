#!/usr/bin/env python3
"""Curated Solidity / EVM coding knowledge for ``ingest_solidity_knowledge.py``.

Curated from the official Solidity docs (docs.soliditylang.org), OpenZeppelin
Contracts, Solidity Patterns (fravoll.github.io/solidity-patterns/) and
Solidity by Example. Covers: syntax, control structures, contracts,
inheritance, libraries, interfaces, events, modifiers, error handling,
assembly/Yul, ABI encoding, storage layout and gas optimization.

These docs guarantee the coding-knowledge collection answers EVM coding
questions even when live doc fetching is unavailable (the ingester merges this
curated set with live-fetched content).
"""

from __future__ import annotations

from typing import Any

# ── Core language docs (official Solidity docs) ──────────────────────

SOLIDITY_LANGUAGE_DOCS: list[dict[str, Any]] = [
    {
        "source": "solidity-docs",
        "rel_key": "language/basics-syntax",
        "title": "Solidity language basics — structure, pragma, comments",
        "topic": "syntax",
        "knowledge_type": "language_docs",
        "content": (
            "Solidity is a statically-typed, contract-oriented language for the EVM. "
            "A source file starts with a pragma and optional SPDX license identifier: "
            "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\n"
            "The pragma restricts the compiler version (^0.8.20 means >=0.8.20 <0.9.0). "
            "Files may contain contract definitions, library definitions, interface "
            "definitions, import directives and function definitions. Use import "
            '"@openzeppelin/contracts/token/ERC20/ERC20.sol"; to pull in dependencies. '
            "Solidity 0.8.x enables checked arithmetic by default (reverts on overflow/"
            "underflow), so SafeMath is no longer needed for 0.8+."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/types",
        "title": "Solidity value types — uint, int, bool, address, bytes, enum, struct",
        "topic": "types",
        "knowledge_type": "language_docs",
        "content": (
            "Solidity value types: bool, int/uint (signed/unsigned integers of 8..256 bits, "
            "e.g. uint256, int128), address (20 bytes, with .balance and .transfer members), "
            "address payable (can receive ether), bytes1..bytes32 (fixed byte arrays), "
            "enum (user-defined), and contract types. Reference types: arrays (uint[]), "
            "mappings (mapping(address => uint256)), structs, and string/bytes (dynamic). "
            "Default values: integers 0, bool false, address 0x0, arrays/mappings empty. "
            "mapping is the most common EVM key-value storage type and can only be stored "
            "in storage, not passed between functions by value (use storage pointers)."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/control-structures",
        "title": "Solidity control structures — if/else, for, while, do-while, ternary",
        "topic": "control-structures",
        "knowledge_type": "language_docs",
        "content": (
            "Solidity supports if/else, for, while, do-while loops and the ternary "
            "operator. Example:\n"
            "for (uint256 i = 0; i < 10; i++) { total += i; }\n"
            "while (balance < threshold) { balance += 1; }\n"
            "There is no switch statement. break and continue work inside loops. "
            "Important: using the same loop variable for many iterations can be "
            "gas-expensive; use unchecked { i++; } inside for loops where overflow is "
            "impossible (Solidity 0.8+) to save gas. Loops over unbounded arrays should "
            "be avoided because every iteration costs gas and a large array can make the "
            "function exceed the block gas limit (see gas optimization)."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/contracts",
        "title": "Solidity contracts — state variables, functions, constructors",
        "topic": "contracts",
        "knowledge_type": "language_docs",
        "content": (
            "A contract is the fundamental Solidity unit. It contains state variables "
            "(persistent storage), functions, modifiers, events, structs and enums. "
            "Example:\n"
            "contract Counter {\n"
            "    uint256 public count;              // state variable\n"
            "    constructor() { count = 0; }        // runs once at deployment\n"
            "    function increment() external { count += 1; }\n"
            "}\n"
            "Function visibility: public (callable by anyone), external (only externally, "
            "cheaper for args), internal (only within the contract and derived), private "
            "(only within the contract). State mutability: view (reads state), pure "
            "(neither reads nor writes state), payable (can receive ether). Constructors "
            "are optional and run at deployment; old 0.4 style used function "
            "Counter() public. Use receive() external payable {} to accept plain ether "
            "and fallback() external for unmatched calls."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/inheritance",
        "title": "Solidity inheritance — is, virtual, override, super, C3 linearization",
        "topic": "inheritance",
        "knowledge_type": "language_docs",
        "content": (
            "Solidity supports single and multiple inheritance with the is keyword. "
            "Functions marked virtual can be overridden with the override keyword:\n"
            "contract Base { function greet() public pure virtual returns (string memory) "
            '{ return "hi"; } }\n'
            "contract Child is Base { function greet() public pure override returns "
            '(string memory) { return "hello"; } }\n'
            "Use super.f() to call the next function in the inheritance chain. "
            "Multiple inheritance uses C3 linearization: the order matters and "
            "contract C is A, B resolves base functions in a specific order. "
            "Constructor arguments of base contracts are passed in the inheritance "
            "list: contract C is A(1), B(2) {}. Diamond problems (same base inherited "
            "twice) are resolved by linearization; state variable shadowing between "
            "bases is disallowed since 0.6. Keep inheritance shallow and prefer "
            "composition (e.g. OpenZeppelin's AccessControl) to avoid complexity bugs."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/libraries",
        "title": "Solidity libraries — reusable code, internal vs external, using-for",
        "topic": "libraries",
        "knowledge_type": "language_docs",
        "content": (
            "Libraries are deployed code that cannot hold ether or state; functions "
            "must be internal (inlined into the calling contract) or external "
            "(delegatecall-based, a separate deployed library). Example:\n"
            "library Math { function max(uint256 a, uint256 b) internal pure returns "
            "(uint256) { return a >= b ? a : b; } }\n"
            "Attach library functions to a type with using-for:\n"
            "using Math for uint256;  // then x.max(y) works\n"
            "OpenZeppelin uses this pattern (e.g. EnumerableSet, SafeCast, Math). "
            "External library calls use DELEGATECALL so the library executes with the "
            "caller's storage context — be careful that a library address must be "
            "trusted (a malicious library can corrupt the caller's storage)."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/interfaces",
        "title": "Solidity interfaces — external function signatures, no implementation",
        "topic": "interfaces",
        "knowledge_type": "language_docs",
        "content": (
            "An interface declares a contract's external functions without "
            "implementation. It cannot have state variables, constructors or "
            "internal functions. Interfaces are the standard way to call other "
            "contracts: \n"
            "interface IERC20 { function transfer(address to, uint256 amount) external "
            "returns (bool); function balanceOf(address account) external view returns "
            "(uint256); }\n"
            "Then: IERC20(tokenAddr).transfer(to, amount); This generates a low-level "
            "call with the 4-byte function selector. To be ERC-165 compatible declare "
            "interface identifiers with supportsInterface(bytes4). OpenZeppelin's "
            "IERC20/IERC721 interfaces define the canonical ERC function sets."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/events",
        "title": "Solidity events — logs, indexed parameters, emitting state changes",
        "topic": "events",
        "knowledge_type": "language_docs",
        "content": (
            "Events are the EVM's logging mechanism, used to inform off-chain "
            "consumers (indexers, UIs, wallets) about state changes. Declaration and "
            "emission:\n"
            "event Transfer(address indexed from, address indexed to, uint256 value);\n"
            "emit Transfer(msg.sender, to, amount);\n"
            "Up to 3 parameters can be indexed (searchable topics); the rest are in the "
            "data field. Events are not accessible on-chain. ERC-20 mandates Transfer "
            "and Approval events. Gas tip: emitting events costs gas, but they are the "
            "only cheap way to make data queryable off-chain — prefer emitting an event "
            "over storing large data blobs. Event names/topics use keccak256 of the "
            "signature, so renaming parameters does NOT change the topic but changing "
            "types does."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/modifiers",
        "title": "Solidity modifiers — access control, guards, nonReentrant pattern",
        "topic": "modifiers",
        "knowledge_type": "language_docs",
        "content": (
            "Modifiers change function behavior and are commonly used for access "
            "control and preconditions. A modifier is declared with modifier and "
            "injected with _ (underscore) at the point the function body runs:\n"
            'modifier onlyOwner() { require(msg.sender == owner, "not owner"); _; }\n'
            "function setFee(uint256 f) external onlyOwner { fee = f; }\n"
            "Modifiers execute before the function body (or at the _ position), so "
            "require checks act as guards. Arguments can be passed: modifier "
            "costsLessThan(uint256 cap) { require(msg.value <= cap); _; }. "
            "Multiple modifiers stack in order. Security: modifier code inlines into "
            "every function, so keep them small; never perform external calls in a "
            "modifier. OpenZeppelin's ReentrancyGuard uses a modifier (nonReentrant) "
            "with a storage status flag to block reentrancy."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/errors",
        "title": "Solidity error handling — require, revert, assert, custom errors",
        "topic": "error-handling",
        "knowledge_type": "language_docs",
        "content": (
            "Solidity handles failures by reverting the transaction. Mechanisms:\n"
            '- require(condition, "msg") — validates inputs/conditions; refunds '
            "remaining gas, reverts state.\n"
            '- revert("msg") / revert CustomError(); — unconditional revert.\n'
            "- assert(condition) — for invariants; consumes all gas (use rarely).\n"
            "Custom errors (Solidity 0.8.4+) save gas and carry data:\n"
            "error InsufficientBalance(uint256 available, uint256 required);\n"
            "revert InsufficientBalance(available, required);\n"
            "try/catch for external calls: try token.transfer(to, amt) returns (bool ok) "
            "{ ... } catch { ... } — the only way to handle failures from external "
            "calls without reverting. require strings are stored in the ABI and cost "
            "gas; prefer custom errors for cheaper reverts."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/assembly-yul",
        "title": "Solidity assembly / Yul — low-level EVM, when to use it",
        "topic": "assembly",
        "knowledge_type": "language_docs",
        "content": (
            "Inline assembly (assembly { ... }) exposes the raw EVM. Use it sparingly "
            "for: gas optimizations (e.g. reading storage with sload), hash "
            "computation (keccak256), ABI decoding of complex data, and interactions "
            "that the high-level language cannot express. Yul example:\n"
            "assembly { let ptr := mload(0x40) mstore(ptr, 42) return(ptr, 32) }\n"
            "Key Yul opcodes: sload/sstore (storage), mload/mstore (memory), calldataload, "
            "call/delegatecall/staticcall, keccak256, add/sub/mul/div, and iszero. "
            "Security: assembly bypasses Solidity's safety (no bounds checks, no type "
            "checks) — bugs here are subtle and a common source of exploits. Prefer "
            "high-level Solidity; keep assembly blocks minimal, documented, and "
            "audited. The free memory pointer (0x40) must be preserved when using "
            "memory."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/abi-encoding",
        "title": "Solidity ABI encoding — abi.encode, abi.encodePacked, decode, selectors",
        "topic": "abi-encoding",
        "knowledge_type": "language_docs",
        "content": (
            "The ABI (Application Binary Interface) defines how function calls and data "
            "are encoded. Key functions:\n"
            "- abi.encode(...) — ABI-encodes arguments (padded to 32-byte words). "
            "Deterministic and unambiguous.\n"
            "- abi.encodePacked(...) — tight packing, no padding; cheaper but can "
            'collide (e.g. abi.encodePacked("a", "bc") == abi.encodePacked("ab", '
            '"c")). NEVER use encodePacked for hash-based signatures without '
            "fixed-length types (ERC-191/712 use it with length bounds).\n"
            "- abi.decode(data, (uint256, address)) — decode call data.\n"
            '- msg.sig / selector: bytes4 selector = bytes4(keccak256("transfer'
            '(address,uint256)"));\n'
            "Function calls encode: 4-byte selector + 32-byte words. Structs/tuples "
            "are supported since 0.8. ABI encoding is used by ERC-712 typed data "
            "signatures and by oracles like Chainlink."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/storage-layout",
        "title": "Solidity storage layout — slots, packing, mappings, structs",
        "topic": "storage-layout",
        "knowledge_type": "language_docs",
        "content": (
            "EVM storage is a 2^256 word-addressed space of 32-byte slots. "
            "State variables are laid out in declaration order, packed: values "
            "smaller than 32 bytes are packed into a single slot when they fit. "
            "uint128 a; uint128 b; → one slot. Mappings and dynamic arrays only "
            "occupy one slot (a placeholder); their elements live at "
            "keccak256(slot) + index. Structs follow their field order. "
            "Storage reads/writes are the dominant gas cost (sload 2100+ warm, "
            "sstore 20000+), so: order variables to pack well, cache storage in "
            "memory/stack inside functions, and avoid storing data you can derive. "
            "Upgradeable-proxy contracts (UUPS/transparent) must NOT declare state "
            "variables after the base — storage collision breaks the proxy."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/gas-optimization",
        "title": "Solidity gas optimization — storage, unchecked, calldata, immutable",
        "topic": "gas-optimization",
        "knowledge_type": "language_docs",
        "content": (
            "Gas optimization reduces transaction cost and failure risk. High-impact "
            "patterns:\n"
            "1. Minimize storage: read storage into memory once; write only when "
            "needed. Use uint256 instead of smaller types unless packing matters "
            "(the EVM charges per slot, not per bit).\n"
            "2. Use unchecked { } for arithmetic proven not to overflow (0.8+): "
            "for (uint256 i; i < n; ) { ...; unchecked { ++i; } }\n"
            "3. calldata instead of memory for external function parameters "
            "(avoids copy).\n"
            "4. immutable / constant for values fixed at deploy time (embedded in "
            "bytecode, no sload).\n"
            "5. Short-circuit require checks and order conditions to fail early.\n"
            "6. Batch operations: update multiple state vars in one function, use "
            "packed structs (uint128+uint128 in one slot).\n"
            "7. Use custom errors instead of require strings.\n"
            "8. Assembly only for proven hot paths (e.g. Uniswap uses bit tricks).\n"
            "Remember: cheaper is not always safer — clarity and auditability matter "
            "more than a few thousand gas."
        ),
    },
    {
        "source": "solidity-docs",
        "rel_key": "language/transactions-messages",
        "title": "Solidity transactions and messages — msg, tx, block globals",
        "topic": "transactions",
        "knowledge_type": "language_docs",
        "content": (
            "Global context variables: msg.sender (immediate caller), msg.value "
            "(wei sent), msg.data, msg.sig; tx.origin (original EOA — NEVER use for "
            "authorization, it enables phishing attacks); block.number (current "
            "block), block.timestamp (approx — miners can manipulate within a few "
            "seconds), blockhash(n); gasleft(); address(this), this.balance; "
            "keccak256/abi.encode for hashing. Security notes: use msg.sender not "
            "tx.origin for access control; don't use block.timestamp for randomness; "
            "don't use block.number to enforce time (use timestamps or better, a "
            "time oracle). payable functions can receive msg.value; require exact "
            'amounts with require(msg.value == price, "exact payment").'
        ),
    },
]

# ── OpenZeppelin docs ─────────────────────────────────────────────────

OPENZEPPELIN_DOCS: list[dict[str, Any]] = [
    {
        "source": "openzeppelin-docs",
        "rel_key": "security/reentrancy-guard",
        "title": "OpenZeppelin ReentrancyGuard — nonReentrant modifier",
        "topic": "reentrancy",
        "knowledge_type": "security_pattern",
        "content": (
            "OpenZeppelin's ReentrancyGuard prevents reentrancy attacks with a "
            "storage status flag (0 = not entered, 1 = entered, 2 = not entered after "
            "an external call) and a modifier:\n"
            'import {ReentrancyGuard} from "@openzeppelin/contracts/utils/'
            'ReentrancyGuard.sol";\n'
            "contract Vault is ReentrancyGuard {\n"
            "    function withdraw(uint256 amount) external nonReentrant {\n"
            '        require(balances[msg.sender] >= amount, "insufficient");\n'
            "        balances[msg.sender] -= amount;   // CEI: update state first\n"
            '        (bool ok, ) = msg.sender.call{value: amount}("");\n'
            '        require(ok, "transfer failed");\n'
            "    }\n"
            "}\n"
            "How it works: nonReentrant sets _status = ENTERED (1) at entry and "
            "reverts if it is already ENTERED; after the function body it sets "
            "_status = NOT_ENTERED (2) using the unlocked pattern, which avoids an "
            "extra sstore and protects against reentrancy through the state "
            "restoration. Best practice: combine nonReentrant with "
            "checks-effects-interactions (CEI) — update all state before any "
            "external call — so even internal callbacks cannot corrupt accounting. "
            "Protect every function that moves value or makes external calls."
        ),
    },
    {
        "source": "openzeppelin-docs",
        "rel_key": "access/ownable",
        "title": "OpenZeppelin Ownable — onlyOwner access control",
        "topic": "access-control",
        "knowledge_type": "security_pattern",
        "content": (
            "OpenZeppelin's Ownable gives a single-owner admin model:\n"
            'import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";\n'
            "contract Token is Ownable { function mint(address to, uint256 amt) "
            "external onlyOwner { _mint(to, amt); } }\n"
            "It provides owner (address), onlyOwner modifier, transferOwnership(newOwner) "
            "and renounceOwnership(). Security guidance: a single owner is a central "
            "point of failure — if the owner key is compromised, the contract is "
            "compromised. Prefer multi-sig (Safe) as owner, use a timelock for "
            "admin functions, and consider AccessControl (roles) instead of a single "
            "owner for finer-grained permissions. Never store the owner in an "
            "upgradeable storage slot that collides with the implementation."
        ),
    },
    {
        "source": "openzeppelin-docs",
        "rel_key": "access/access-control",
        "title": "OpenZeppelin AccessControl — role-based access control (RBAC)",
        "topic": "access-control",
        "knowledge_type": "security_pattern",
        "content": (
            "OpenZeppelin AccessControl implements role-based access control with "
            "bytes32 roles:\n"
            'import {AccessControl} from "@openzeppelin/contracts/access/'
            'AccessControl.sol";\n'
            'bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");\n'
            "contract MyToken is AccessControl {\n"
            "    constructor() { _grantRole(DEFAULT_ADMIN_ROLE, msg.sender); }\n"
            "    function mint(address to, uint256 amt) external onlyRole(MINTER_ROLE) "
            "{ _mint(to, amt); }\n"
            "}\n"
            "Roles: DEFAULT_ADMIN_ROLE can grant/revoke roles via grantRole/revokeRole/"
            "renounceRole; hasRole checks membership. Use keccak256-based role ids for "
            "readability. Best practice: use AccessControl instead of single-owner "
            "for multi-actor systems (minter, burner, pauser, admin), and use "
            "AccessControlDefaultAdminRules (which adds a 2-step admin transfer) to "
            "avoid permanently locking out admin access."
        ),
    },
    {
        "source": "openzeppelin-docs",
        "rel_key": "tokens/erc20",
        "title": "OpenZeppelin ERC20 — standard token, _mint/_burn, overrides",
        "topic": "erc20",
        "knowledge_type": "framework_docs",
        "content": (
            "The canonical ERC-20 implementation:\n"
            'import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";\n'
            'import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";\n'
            "contract MyToken is ERC20, Ownable {\n"
            '    constructor() ERC20("MyToken", "MTK") { _mint(msg.sender, 1e24); }\n'
            "    function mint(address to, uint256 amt) external onlyOwner { "
            "_mint(to, amt); }\n"
            "}\n"
            "Key internals: _mint/_burn update totalSupply and balanceOf and emit "
            "Transfer (including zero-address mints/burns per ERC-20 spec); "
            "_transfer enforces the standard; _spendAllowance handles the "
            "allowance/approve flow. Security: never leave an unguarded public mint; "
            "consider ERC20Permit (EIP-2612) for gasless approvals; beware fee-on-"
            "transfer tokens when integrating; check return values (use SafeERC20's "
            "safeTransfer for tokens that don't return bool)."
        ),
    },
    {
        "source": "openzeppelin-docs",
        "rel_key": "tokens/erc721",
        "title": "OpenZeppelin ERC721 — NFTs, safeTransferFrom, IERC721Receiver",
        "topic": "erc721",
        "knowledge_type": "framework_docs",
        "content": (
            "ERC-721 is the NFT standard. OpenZeppelin's implementation provides "
            "mint/burn/safeTransferFrom plus extensions (ERC721Enumerable, "
            "ERC721URIStorage, ERC721Royalty). Safe transfers call "
            "onERC721Received on the recipient, which must implement "
            "IERC721Receiver or the transfer reverts (prevents NFTs being stuck). "
            "Key rules: transferFrom with an untrusted recipient loses the NFT; "
            "approve/setApprovalForAll control who can move tokens; never burn user "
            "NFTs without authorization; use safeMint (which uses safeTransferFrom) "
            "for minting to arbitrary addresses. Security: validate the receiver "
            "implements the receiver interface, and treat operator approvals as "
            "high-value targets for phishing."
        ),
    },
    {
        "source": "openzeppelin-docs",
        "rel_key": "proxies/upgradeable",
        "title": "OpenZeppelin upgradeable proxies — UUPS, transparent, storage layout",
        "topic": "proxies",
        "knowledge_type": "framework_docs",
        "content": (
            "Upgradeable contracts use a proxy that delegates calls to an "
            "implementation while storing state in the proxy. Patterns: transparent "
            "proxy (admin functions go to the proxy, all others delegate) and UUPS "
            "(upgrade logic lives in the implementation via "
            "upgradeToAndCall). Requirements: the implementation MUST use "
            "initializer functions instead of constructors (constructor code runs in "
            "the implementation, not the proxy):\n"
            "function initialize(address owner_) external initializer { __Ownable_init(); "
            "owner = owner_; }\n"
            "Storage layout can NEVER change (adding a variable is OK only at the "
            "end; reordering/removing breaks existing state). Use "
            "@openzeppelin/contracts-upgradeable/ versions of contracts. Security: "
            "uninitialized implementations can be self-destructed (check the "
            "implementation is disabled), and upgrade authority must be tightly "
            "controlled (timelock + multisig)."
        ),
    },
]

# ── Solidity patterns (fravoll) ───────────────────────────────────────

SOLIDITY_PATTERNS_DOCS: list[dict[str, Any]] = [
    {
        "source": "solidity-patterns",
        "rel_key": "security/checks-effects-interactions",
        "title": "Checks-Effects-Interactions (CEI) pattern",
        "topic": "reentrancy",
        "knowledge_type": "security_pattern",
        "content": (
            "The Checks-Effects-Interactions pattern is the first line of defense "
            "against reentrancy:\n"
            "1. Checks — validate inputs/state (require/if).\n"
            "2. Effects — update the contract's own state.\n"
            "3. Interactions — make external calls last.\n"
            "Example:\n"
            "function withdraw(uint256 amount) external {\n"
            '    require(balances[msg.sender] >= amount, "insufficient");  // Checks\n'
            "    balances[msg.sender] -= amount;   // Effects (state first!)\n"
            '    (bool ok, ) = msg.sender.call{value: amount}("");  // Interactions\n'
            '    require(ok, "transfer failed");\n'
            "}\n"
            "Because the balance is decremented BEFORE the external call, a "
            "malicious reentrant call sees the new (lower) balance and cannot "
            "double-withdraw. Combine with a reentrancy guard for defense in depth, "
            "especially when interactions happen in loops or callbacks."
        ),
    },
    {
        "source": "solidity-patterns",
        "rel_key": "security/withdrawal",
        "title": "Withdrawal pattern — pull payments instead of push",
        "topic": "withdrawal",
        "knowledge_type": "security_pattern",
        "content": (
            "The withdrawal pattern (pull over push) avoids sending ether "
            "automatically. Instead of pushing funds to users (which can fail or be "
            "reentered), let users claim their balance:\n"
            "mapping(address => uint256) public balances;\n"
            "function withdraw() external {\n"
            "    uint256 amount = balances[msg.sender];\n"
            '    require(amount > 0, "nothing to withdraw");\n'
            "    balances[msg.sender] = 0;   // CEI: zero first\n"
            '    (bool ok, ) = msg.sender.call{value: amount}("");\n'
            '    require(ok, "transfer failed");\n'
            "}\n"
            "Benefits: no failed push loops, no gas griefing on arrays of payees, "
            "users control when they claim, and it naturally satisfies CEI. "
            "OpenZeppelin's PullPayment implements this. Only use push payments for "
            "trusted, fixed recipients."
        ),
    },
    {
        "source": "solidity-patterns",
        "rel_key": "security/emergency-stop",
        "title": "Emergency stop / circuit breaker pattern",
        "topic": "emergency-stop",
        "knowledge_type": "security_pattern",
        "content": (
            "A circuit breaker lets an authorized role pause critical functions "
            "during an incident. OpenZeppelin Pausable:\n"
            'import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";\n'
            "contract Fund is Pausable {\n"
            "    function withdraw() external whenNotPaused { ... }\n"
            "    function pause() external onlyOwner { _pause(); }\n"
            "    function unpause() external onlyOwner { _unpause(); }\n"
            "}\n"
            "Design decisions: pausing withdrawals but not deposits is a common "
            "emergency posture; pausing everything can lock user funds. Consider "
            "timelocked pausing (a delay before pause takes effect) to avoid a "
            "malicious admin rugging users. Circuit breakers are a mitigation, not a "
            "fix — combine with monitoring and a rapid response plan."
        ),
    },
    {
        "source": "solidity-patterns",
        "rel_key": "security/access-restriction",
        "title": "Access restriction pattern — onlyOwner, role guards",
        "topic": "access-control",
        "knowledge_type": "security_pattern",
        "content": (
            "Restrict sensitive functions with modifiers. The classic pattern:\n"
            'modifier onlyOwner() { require(msg.sender == owner, "not authorized"); _; }\n'
            "function setFee(uint256 fee_) external onlyOwner { fee = fee_; }\n"
            "For multi-role systems use OpenZeppelin AccessControl (onlyRole). "
            "Rules: never rely on tx.origin; make the admin a multisig/timelock; "
            "audit every function that changes parameters or moves funds; prefer "
            "granular roles over one god-mode key. Consider a two-step ownership "
            "transfer so a typo in the new owner address does not lock the contract."
        ),
    },
    {
        "source": "solidity-patterns",
        "rel_key": "security/secure-randomness",
        "title": "Secure randomness — never use block.timestamp/blockhash, use VRF",
        "topic": "randomness",
        "knowledge_type": "security_pattern",
        "content": (
            "On-chain randomness is impossible to generate natively: block.timestamp, "
            "blockhash, and block.difficulty are predictable or miner-influenced and "
            "enable lottery/raffle exploits. Use a verifiable random function: "
            "Chainlink VRF (requestRandomness / requestRandomWords) or commit-"
            "reveal schemes. If you must use a heuristic, combine multiple "
            "hard-to-predict inputs and accept the bias. Example with Chainlink VRF: "
            "request a random word, store the request id, fulfill with the random "
            "number, then use it to select the winner. Never trust user-supplied "
            "randomness."
        ),
    },
]

# ── Solidity by Example style code snippets ───────────────────────────

SOLIDITY_BY_EXAMPLE_DOCS: list[dict[str, Any]] = [
    {
        "source": "solidity-by-example",
        "rel_key": "examples/reentrancy-guard",
        "title": "Reentrancy guard by example — mutex with storage flag",
        "topic": "reentrancy",
        "knowledge_type": "code_example",
        "content": (
            "A minimal reentrancy guard (mutex) without OpenZeppelin:\n"
            "contract Guarded {\n"
            "    bool private locked;\n"
            "    modifier noReentrant() {\n"
            '        require(!locked, "reentrant call");\n'
            "        locked = true;\n"
            "        _;\n"
            "        locked = false;\n"
            "    }\n"
            "    function withdraw(uint256 amount) external noReentrant {\n"
            "        require(balances[msg.sender] >= amount);\n"
            "        balances[msg.sender] -= amount;\n"
            '        (bool ok, ) = msg.sender.call{value: amount}("");\n'
            "        require(ok);\n"
            "    }\n"
            "}\n"
            "The bool locked flag reverts any nested entry. Note: OpenZeppelin's "
            "ReentrancyGuard uses a more gas-efficient uint256 status (1=entered, "
            "2=not-entered) to avoid the extra sstore on the unlock path. Always "
            "pair a guard with CEI ordering."
        ),
    },
    {
        "source": "solidity-by-example",
        "rel_key": "examples/erc20-minimal",
        "title": "Minimal ERC-20 implementation by example",
        "topic": "erc20",
        "knowledge_type": "code_example",
        "content": (
            "Minimal ERC-20:\n"
            "contract ERC20 {\n"
            "    string public name; string public symbol; uint8 public decimals = 18;\n"
            "    uint256 public totalSupply;\n"
            "    mapping(address => uint256) public balanceOf;\n"
            "    mapping(address => mapping(address => uint256)) public allowance;\n"
            "    event Transfer(address indexed from, address indexed to, uint256 value);\n"
            "    event Approval(address indexed owner, address indexed spender, uint256 value);\n"
            "    function transfer(address to, uint256 value) external returns (bool) {\n"
            "        return _transfer(msg.sender, to, value);\n"
            "    }\n"
            "    function approve(address spender, uint256 value) external returns (bool) {\n"
            "        allowance[msg.sender][spender] = value;\n"
            "        emit Approval(msg.sender, spender, value);\n"
            "        return true;\n"
            "    }\n"
            "    function transferFrom(address from, address to, uint256 value) external "
            "returns (bool) {\n"
            '        require(allowance[from][msg.sender] >= value, "allowance");\n'
            "        allowance[from][msg.sender] -= value;\n"
            "        return _transfer(from, to, value);\n"
            "    }\n"
            "    function _transfer(address from, address to, uint256 value) internal "
            "returns (bool) {\n"
            '        require(balanceOf[from] >= value, "balance");\n'
            "        balanceOf[from] -= value;\n"
            "        balanceOf[to] += value;\n"
            "        emit Transfer(from, to, value);\n"
            "        return true;\n"
            "    }\n"
            "}\n"
            "For production use OpenZeppelin's audited ERC20 instead of reimplementing."
        ),
    },
    {
        "source": "solidity-by-example",
        "rel_key": "examples/events",
        "title": "Events by example — emitting and indexing state changes",
        "topic": "events",
        "knowledge_type": "code_example",
        "content": (
            "Events let off-chain code react to on-chain state changes:\n"
            "event Deposited(address indexed user, uint256 amount, uint256 newBalance);\n"
            "function deposit() external payable {\n"
            "    balances[msg.sender] += msg.value;\n"
            "    emit Deposited(msg.sender, msg.value, balances[msg.sender]);\n"
            "}\n"
            "Use indexed for the first 3 parameters you want to filter on (they "
            "become searchable topics). The EVM stores event logs in the transaction "
            "receipt, retrievable via eth_getLogs. Indexers (The Graph, Ponder) "
            "listen to these topics to build databases."
        ),
    },
    {
        "source": "solidity-by-example",
        "rel_key": "examples/inheritance-override",
        "title": "Inheritance and override by example",
        "topic": "inheritance",
        "knowledge_type": "code_example",
        "content": (
            "contract Owned { address public owner; constructor() { owner = msg.sender; } "
            "modifier onlyOwner() { require(msg.sender == owner); _; } }\n"
            "contract Mortal is Owned { function destroy() external onlyOwner { "
            "selfdestruct(payable(owner)); } }\n"
            "contract Base { function foo() public pure virtual returns (uint256) { "
            "return 1; } }\n"
            "contract Derived is Base { function foo() public pure override returns "
            "(uint256) { return 2; } }\n"
            "virtual/override are mandatory since Solidity 0.6. selfdestruct is "
            "deprecated as of the Cancun upgrade (EIP-6780) and only clears the "
            "balance of the calling contract in the same transaction."
        ),
    },
    {
        "source": "solidity-by-example",
        "rel_key": "examples/structs-mappings",
        "title": "Structs and mappings by example",
        "topic": "types",
        "knowledge_type": "code_example",
        "content": (
            "struct Payment { address to; uint256 amount; bool paid; }\n"
            "mapping(address => Payment[]) public payments;\n"
            "function addPayment(address to, uint256 amount) external {\n"
            "    payments[msg.sender].push(Payment(to, amount, false));\n"
            "}\n"
            "function claim(uint256 index) external {\n"
            "    Payment storage p = payments[msg.sender][index];\n"
            '    require(!p.paid, "already claimed");\n'
            "    p.paid = true;\n"
            '    (bool ok, ) = p.to.call{value: p.amount}("");\n'
            "    require(ok);\n"
            "}\n"
            "Note the storage pointer (Payment storage p) — modifying it writes "
            "straight to storage without copying. Struct packing: order fields by "
            "size to pack into fewer slots (uint128 then uint128 fits one slot)."
        ),
    },
]

ALL_SOLIDITY_DOCS: list[dict[str, Any]] = (
    SOLIDITY_LANGUAGE_DOCS + OPENZEPPELIN_DOCS + SOLIDITY_PATTERNS_DOCS + SOLIDITY_BY_EXAMPLE_DOCS
)
