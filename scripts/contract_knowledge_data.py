#!/usr/bin/env python3
"""Curated crypto security-pattern knowledge for ``ingest_security_patterns.py``.

Three complementary layers:

    SECURITY_PATTERNS — reusable vulnerability classes for Ethereum and Solana,
        each with vulnerable + fixed code, detection heuristics and severity.
    SWC_FALLBACKS     — a doc per SWC-100..136 (completes the SWC Registry
        coverage inside the ``contract_security`` collection; full entries are
        also ingested into ``threat_intel`` by ingest_swc_registry.py).
    AUDIT_FINDINGS    — real exploit/audit case studies (Euler, Mango, Wormhole,
        ...) for the ``audit_findings`` collection.

Sources: SWC Registry, public audit reports (Trail of Bits, OpenZeppelin,
Consensys Diligence), DeFiHackLabs PoCs, and public incident post-mortems.
"""

from __future__ import annotations

from typing import Any

# ── Ethereum + Solana security patterns ────────────────────────────────

SECURITY_PATTERNS: list[dict[str, Any]] = [
    # ── Ethereum / EVM ──────────────────────────────────────────────
    {
        "pattern_id": "evm:reentrancy-single-function",
        "title": "Reentrancy — external call before state update",
        "ecosystem": "ethereum",
        "category": "reentrancy",
        "swc": "SWC-107",
        "severity": "critical",
        "vulnerable_code": (
            "function withdraw(uint256 amount) external {\n"
            "    require(balances[msg.sender] >= amount);\n"
            '    (bool ok, ) = msg.sender.call{value: amount}("");\n'
            '    require(ok, "transfer failed");\n'
            "    balances[msg.sender] -= amount;  // state updated AFTER the call\n"
            "}"
        ),
        "fixed_code": (
            "function withdraw(uint256 amount) external nonReentrant {\n"
            "    require(balances[msg.sender] >= amount);\n"
            "    balances[msg.sender] -= amount;  // CEI: update state first\n"
            '    (bool ok, ) = msg.sender.call{value: amount}("");\n'
            '    require(ok, "transfer failed");\n'
            "}"
        ),
        "description": (
            "The classic DAO-hack vulnerability: an external call (call/transfer/"
            "send, or a token transfer to an untrusted contract) executes before "
            "the caller's state is updated. A malicious receiver re-enters the "
            "function and drains funds because the balance is still the old value."
        ),
        "detection_heuristics": [
            "external call (.call{value:}, .transfer(), token transfer) appears before a state write in the same function",
            "no nonReentrant modifier / ReentrancyGuard on value-moving functions",
            "mapping balances updated after an external interaction",
        ],
        "references": [
            "https://swcregistry.io/docs/SWC-107",
            "https://github.com/SunWeb3Sec/DeFiHackLabs",
        ],
    },
    {
        "pattern_id": "evm:reentrancy-cross-function",
        "title": "Cross-function reentrancy",
        "ecosystem": "ethereum",
        "category": "reentrancy",
        "swc": "SWC-107",
        "severity": "critical",
        "vulnerable_code": (
            "function transfer(address to, uint256 amount) external {\n"
            "    _update(msg.sender, to, amount);\n"
            "    // state updated, but a malicious recipient can call withdraw()\n"
            "    // during the IERC20 callback if balances are inconsistent\n"
            "    IERC20Callback(to).onTransfer(msg.sender, amount);\n"
            "}\n"
            "function withdraw() external { /* uses balances */ }"
        ),
        "fixed_code": (
            "// Use a global reentrancy guard and never invoke untrusted\n"
            "// callbacks before/while internal invariants can be violated.\n"
            "function transfer(address to, uint256 amount) external nonReentrant { ... }"
        ),
        "description": (
            "Reentrancy across different functions: a victim contract shares "
            "state between functions, and an attacker uses a callback/hook to "
            "call a second function that reads state the first function has not "
            "yet finalized (e.g. NFT hooks, ERC777/ERC1155 callbacks)."
        ),
        "detection_heuristics": [
            "external callbacks (ERC777 tokensReceived, ERC1155 onERC1155Received, custom hooks) invoked while internal state is mutable",
            "multiple functions mutate the same storage without a shared guard",
        ],
        "references": ["https://swcregistry.io/docs/SWC-107", "https://rekt.news/"],
    },
    {
        "pattern_id": "evm:read-only-reentrancy",
        "title": "Read-only reentrancy (view function state inconsistency)",
        "ecosystem": "ethereum",
        "category": "reentrancy",
        "swc": "SWC-107",
        "severity": "high",
        "vulnerable_code": (
            "function balanceOf(address a) external view returns (uint256) {\n"
            "    return balances[a];  // stale during reentrancy on the write path\n"
            "}"
        ),
        "fixed_code": (
            "// Guard state-changing functions and snapshot values; integrators\n"
            "// should not rely on view functions during an in-flight tx.\n"
            "function balanceOf(address a) external view returns (uint256) {\n"
            "    return balances[a];\n"
            "}"
        ),
        "description": (
            "Attacker re-enters a state-changing function while a protocol's "
            "view/read functions return stale values. Protocols that read "
            "external balances mid-transaction (liquidation bots, oracles) "
            "can be manipulated. Mitigate with guards and consistent "
            "CEI ordering."
        ),
        "detection_heuristics": [
            "external view/read of another contract's balances inside a state-changing flow",
            "no reentrancy guard on the state-changing path",
        ],
        "references": ["https://www.paradigm.xyz/2021/07/a-guide-to-defi-risk"],
    },
    {
        "pattern_id": "evm:tx-origin-auth",
        "title": "tx.origin used for authorization",
        "ecosystem": "ethereum",
        "category": "access-control",
        "swc": "SWC-115",
        "severity": "critical",
        "vulnerable_code": (
            "function withdraw() external {\n"
            '    require(tx.origin == owner, "not owner");\n'
            "    payable(owner).transfer(address(this).balance);\n"
            "}"
        ),
        "fixed_code": (
            "function withdraw() external onlyOwner {\n"
            "    payable(owner()).transfer(address(this).balance);\n"
            "}"
        ),
        "description": (
            "tx.origin is the original external caller, not msg.sender. A "
            "phishing contract can call an innocent contract on the user's "
            "behalf and pass the tx.origin check (the user is still the "
            "origin). Always authorize with msg.sender."
        ),
        "detection_heuristics": ["tx.origin in require/if/== comparisons"],
        "references": ["https://swcregistry.io/docs/SWC-115"],
    },
    {
        "pattern_id": "evm:missing-access-control",
        "title": "Missing access control on privileged functions",
        "ecosystem": "ethereum",
        "category": "access-control",
        "swc": "SWC-105",
        "severity": "critical",
        "vulnerable_code": (
            "function setTax(uint256 newTax) external {  // anyone can call\n"
            "    taxBps = newTax;\n"
            "}\n"
            "function mint(address to, uint256 amount) external {  // anyone can mint\n"
            "    _mint(to, amount);\n"
            "}"
        ),
        "fixed_code": (
            "function setTax(uint256 newTax) external onlyOwner {\n"
            "    taxBps = newTax;\n"
            "}\n"
            "function mint(address to, uint256 amount) external onlyRole(MINTER_ROLE) {\n"
            "    _mint(to, amount);\n"
            "}"
        ),
        "description": (
            "Privileged operations (mint, setFee, withdraw, setImplementation, "
            "transferOwnership) missing onlyOwner/onlyRole guards. Any caller "
            "can seize control, mint unlimited supply, or change parameters."
        ),
        "detection_heuristics": [
            "public/external function that mutates privileged state without an owner/role check",
            "mint/withdraw/setFee/setAdmin/upgrade functions lacking onlyOwner/onlyRole",
        ],
        "references": [
            "https://swcregistry.io/docs/SWC-105",
            "https://swcregistry.io/docs/SWC-106",
        ],
    },
    {
        "pattern_id": "evm:oracle-manipulation",
        "title": "Oracle manipulation via flash loan / single-source price",
        "ecosystem": "ethereum",
        "category": "oracle-manipulation",
        "swc": "SWC-116",
        "severity": "critical",
        "vulnerable_code": (
            "function getCollateralValue(address token, uint256 amount) external view returns (uint256) {\n"
            "    uint256 price = IUniswapV2Pair(pairFor(token)).getReserves();\n"
            "    // spot price from a single thin pool — flash-loan manipulable\n"
            "    return (amount * price) / 1e18;\n"
            "}"
        ),
        "fixed_code": (
            "function getCollateralValue(address token, uint256 amount) external view returns (uint256) {\n"
            "    (, int256 answer, , uint256 updatedAt, ) = priceFeed.latestRoundData();\n"
            '    require(block.timestamp - updatedAt < STALE_AFTER, "stale price");\n'
            '    require(answer > 0, "bad price");\n'
            "    return (amount * uint256(answer)) / 1e8;\n"
            "}"
        ),
        "description": (
            "Prices derived from a single AMM pool (or a thin/illiquid pool) "
            "can be manipulated with a flash loan: swap the pool, trigger the "
            "protocol's price read, drain, then swap back. Mango Markets "
            "($114M) and countless lending exploits used this. Mitigations: "
            "Chainlink TWAP with staleness checks, multiple sources, "
            "time-weighted average prices."
        ),
        "detection_heuristics": [
            "spot price read directly from a pool's getReserves()/sqrtPriceX96",
            "no staleness/price-bound check on oracle answers",
            "liquidation/collateral math using a single spot price",
        ],
        "references": [
            "https://swcregistry.io/docs/SWC-116",
            "https://github.com/SunWeb3Sec/DeFiHackLabs",
        ],
    },
    {
        "pattern_id": "evm:flash-loan-attack",
        "title": "Flash-loan enabled exploit primitives",
        "ecosystem": "ethereum",
        "category": "flash-loan",
        "swc": "SWC-116",
        "severity": "critical",
        "vulnerable_code": (
            "// Borrowing huge capital without collateral for one tx and using\n"
            "// it to dominate a market/price/liquidation — the root primitive\n"
            "// behind most 2020-2024 DeFi exploits.\n"
            "uniswapV2Pair.swap(0, bigAmount, address(this), abi.encode());"
        ),
        "fixed_code": (
            "// Protocol-side: TWAP prices, borrow caps, per-block limits,\n"
            "// liquidation bonuses, and invariant checks that survive\n"
            "// one-tx capital concentration."
        ),
        "description": (
            "Flash loans are a legitimate primitive but amplify every "
            "price-manipulation/accounting bug. When auditing, ask: what can "
            "an attacker do with unbounded one-tx capital? Euler ($197M), "
            "Cream, and many others fell to flash-loan-accelerated exploits."
        ),
        "detection_heuristics": [
            "price/collateral math readable by a single-tx borrower",
            "no per-block or per-address exposure caps",
        ],
        "references": ["https://github.com/SunWeb3Sec/DeFiHackLabs"],
    },
    {
        "pattern_id": "evm:integer-overflow-underflow",
        "title": "Integer overflow / underflow (pre-0.8 arithmetic)",
        "ecosystem": "ethereum",
        "category": "integer-overflow",
        "swc": "SWC-101",
        "severity": "high",
        "vulnerable_code": (
            "// Solidity < 0.8 wraps silently\n"
            "function transfer(address to, uint256 amount) external {\n"
            "    balances[msg.sender] -= amount;  // underflow -> huge balance\n"
            "    balances[to] += amount;\n"
            "}"
        ),
        "fixed_code": (
            "// Solidity >= 0.8 reverts on overflow/underflow automatically,\n"
            "// or use SafeMath / checked arithmetic for older compilers.\n"
            "function transfer(address to, uint256 amount) external {\n"
            '    require(balances[msg.sender] >= amount, "insufficient");\n'
            "    balances[msg.sender] -= amount;\n"
            "    balances[to] += amount;\n"
            "}"
        ),
        "description": (
            "In Solidity < 0.8, arithmetic wraps silently. Underflow can mint "
            "huge balances; overflow can zero out accounting. Modern code "
            "should target >= 0.8 or use checked math everywhere."
        ),
        "detection_heuristics": [
            "pragma solidity <0.8.0 without SafeMath",
            "unchecked arithmetic on balances/amounts",
        ],
        "references": ["https://swcregistry.io/docs/SWC-101"],
    },
    {
        "pattern_id": "evm:unchecked-external-call",
        "title": "Unchecked low-level call return value",
        "ecosystem": "ethereum",
        "category": "input-validation",
        "swc": "SWC-104",
        "severity": "high",
        "vulnerable_code": (
            "function withdraw(uint256 amount) external {\n"
            '    msg.sender.call{value: amount}("");  // return value ignored\n'
            "    balances[msg.sender] -= amount;\n"
            "}"
        ),
        "fixed_code": (
            "function withdraw(uint256 amount) external {\n"
            '    (bool ok, ) = msg.sender.call{value: amount}("");\n'
            '    require(ok, "transfer failed");\n'
            "    balances[msg.sender] -= amount;\n"
            "}"
        ),
        "description": (
            "Ignoring the boolean returned by low-level call/delegatecall lets "
            "a failing transfer silently continue (state marked transferred, "
            "funds never sent). Always require(ok) — or prefer safe "
            "high-level transfers with CEI ordering."
        ),
        "detection_heuristics": [
            "bare .call{...}(...) without capturing/checking the bool",
            "transfer/send used with .call that swallows failures",
        ],
        "references": ["https://swcregistry.io/docs/SWC-104"],
    },
    {
        "pattern_id": "evm:delegatecall-arbitrary",
        "title": "Delegatecall to attacker-controlled address",
        "ecosystem": "ethereum",
        "category": "access-control",
        "swc": "SWC-112",
        "severity": "critical",
        "vulnerable_code": (
            "function execute(address target, bytes calldata data) external {\n"
            "    target.delegatecall(data);  // runs in THIS contract's context\n"
            "}"
        ),
        "fixed_code": (
            "// Only allow pre-registered, trusted implementations and always\n"
            "// check the caller (e.g. onlyOwner) before any delegatecall.\n"
            "function upgrade(address newImpl) external onlyOwner {\n"
            '    require(isTrustedImpl[newImpl], "untrusted impl");\n'
            "    _setImplementation(newImpl);\n"
            "}"
        ),
        "description": (
            "delegatecall executes callee code in the caller's storage/context. "
            "If an attacker can influence the target, they can overwrite "
            "owner/slot0 and take over the contract. The Parity multi-sig "
            "freeze ($280M) was a delegatecall selfdestruct."
        ),
        "detection_heuristics": [
            "delegatecall with a target derived from user input",
            "proxy upgrade functions without whitelist/timelock",
        ],
        "references": [
            "https://swcregistry.io/docs/SWC-112",
            "https://www.parity.io/blog/what-happened",
        ],
    },
    {
        "pattern_id": "evm:selfdestruct",
        "title": "selfdestruct / contract destruction",
        "ecosystem": "ethereum",
        "category": "access-control",
        "swc": "SWC-106",
        "severity": "critical",
        "vulnerable_code": (
            "function kill() external {\n"
            "    selfdestruct(payable(owner));  // if reachable by attacker: catastrophic\n"
            "}"
        ),
        "fixed_code": (
            "// Remove selfdestruct entirely; or gate behind a multisig + timelock\n"
            "// with a long delay and keep an emergency pause separate."
        ),
        "description": (
            "selfdestruct sends the contract's entire balance and removes code. "
            "If callable by anyone (missing access control, or reachable via a "
            "delegatecall to a library containing it), funds and functionality "
            "are destroyed."
        ),
        "detection_heuristics": [
            "selfdestruct in any reachable code path",
            "SUICIDE opcode in bytecode",
        ],
        "references": ["https://swcregistry.io/docs/SWC-106"],
    },
    {
        "pattern_id": "evm:uninitialized-proxy",
        "title": "Uninitialized upgradeable proxy / missing initializer guard",
        "ecosystem": "ethereum",
        "category": "access-control",
        "swc": "SWC-109",
        "severity": "critical",
        "vulnerable_code": (
            "// Proxy can be initialized by anyone before the deployer does\n"
            "contract Upgradeable is Initializable {\n"
            "    function initialize(address _owner) public initializer {\n"
            "        owner = _owner;\n"
            "    }\n"
            "}"
        ),
        "fixed_code": (
            "// Call initialize() in the same tx as deployment (deploy-and-call),\n"
            "// or use a constructor guard that blocks all post-deploy calls.\n"
            "constructor() { _disableInitializers(); }"
        ),
        "description": (
            "If an upgradeable contract's initializer is not protected (and not "
            "called atomically at deploy), anyone can call it first and become "
            "owner/admin of the proxy."
        ),
        "detection_heuristics": [
            "public initialize() without initializer guard in an upgradeable context",
            "proxy deployed without atomic constructor+initialize",
        ],
        "references": ["https://swcregistry.io/docs/SWC-109"],
    },
    {
        "pattern_id": "evm:signature-replay",
        "title": "Signature replay / missing nonce",
        "ecosystem": "ethereum",
        "category": "signature-malleability",
        "swc": "SWC-121",
        "severity": "high",
        "vulnerable_code": (
            "function permit(address owner, address spender, uint256 value,\n"
            "                 uint256 deadline, bytes calldata sig) external {\n"
            "    // no nonce, no deadline expiry enforcement -> replayable\n"
            "    address recovered = ecrecover(hash(owner, spender, value), sig);\n"
            "    require(recovered == owner);\n"
            "    allowance[owner][spender] = value;\n"
            "}"
        ),
        "fixed_code": (
            "// Include a per-user nonce + domain separator + deadline, and\n"
            "// increment the nonce on use so the signature is single-use.\n"
            "function permit(...) external {\n"
            '    require(block.timestamp <= deadline, "expired");\n'
            "    uint256 nonce = nonces[owner]++;\n"
            '    bytes32 digest = keccak256(abi.encodePacked("\\x19\\x01", DOMAIN_SEPARATOR, hash(owner, spender, value, nonce, deadline)));\n'
            '    require(ecrecover(digest, sig) == owner, "bad sig");\n'
            "    allowance[owner][spender] = value;\n"
            "}"
        ),
        "description": (
            "Signatures that lack a nonce/domain/expiry can be replayed across "
            "chains, forks, or multiple times. Always use EIP-712 with nonces "
            "and deadlines."
        ),
        "detection_heuristics": [
            "ecrecover without nonce/domain separator",
            "no deadline check on signed approvals",
        ],
        "references": [
            "https://swcregistry.io/docs/SWC-121",
            "https://swcregistry.io/docs/SWC-122",
        ],
    },
    {
        "pattern_id": "evm:signature-malleability",
        "title": "ECDSA signature malleability (ecrecover)",
        "ecosystem": "ethereum",
        "category": "signature-malleability",
        "swc": "SWC-117",
        "severity": "medium",
        "vulnerable_code": (
            "function recoverSigner(bytes32 digest, bytes calldata sig) external pure returns (address) {\n"
            "    return ecrecover(digest, uint8(sig[64]), uint256(bytes32(sig[0:32])), uint256(bytes32(sig[32:64])));\n"
            "}"
        ),
        "fixed_code": (
            "// Use OpenZeppelin ECDSA.recover which enforces low-s + malleability\n"
            "// checks, and validate against expected signer.\n"
            "address signer = ECDSA.recover(digest, sig);"
        ),
        "description": (
            "The same ECDSA signature can exist in two valid (s, 27/28) forms. "
            "If a protocol keyed state on the raw signature, an attacker could "
            "submit the malleated variant to bypass dedup checks."
        ),
        "detection_heuristics": [
            "raw ecrecover without OpenZeppelin ECDSA wrapper",
            "signature used as a map key",
        ],
        "references": ["https://swcregistry.io/docs/SWC-117"],
    },
    {
        "pattern_id": "evm:dos-block-gas-limit",
        "title": "Denial of service — unbounded loops / gas griefing",
        "ecosystem": "ethereum",
        "category": "denial-of-service",
        "swc": "SWC-128",
        "severity": "high",
        "vulnerable_code": (
            "function distributeRewards() external {\n"
            "    for (uint256 i = 0; i < holders.length; i++) {\n"
            "        pay(holders[i], rewards[i]);  // unbounded loop over storage array\n"
            "    }\n"
            "}"
        ),
        "fixed_code": (
            "// Batch/paginate loops, allow skipping failed recipients, and\n"
            "// never iterate over unbounded arrays in a single tx.\n"
            "function distributeRewards(uint256 from, uint256 to) external { ... }"
        ),
        "description": (
            "Loops over unbounded arrays, or requiring each iteration to "
            "succeed, allow an attacker to make the function uncallable "
            "(block gas limit) or grief one failing recipient to block "
            "everyone (e.g. push-only payment systems)."
        ),
        "detection_heuristics": [
            "for/while over storage arrays of unbounded length",
            "payment loop that reverts the whole tx on one bad recipient",
        ],
        "references": [
            "https://swcregistry.io/docs/SWC-128",
            "https://swcregistry.io/docs/SWC-113",
        ],
    },
    {
        "pattern_id": "evm:block-timestamp",
        "title": "Block timestamp dependence",
        "ecosystem": "ethereum",
        "category": "input-validation",
        "swc": "SWC-116",
        "severity": "medium",
        "vulnerable_code": (
            "function random() external view returns (uint256) {\n"
            "    return uint256(keccak256(abi.encodePacked(block.timestamp, block.difficulty)));\n"
            "}"
        ),
        "fixed_code": (
            "// Use Chainlink VRF for on-chain randomness; timestamps are only\n"
            "// safe for coarse time bounds (deadlines), not randomness.\n"
            "uint256 requestId = VRFCoordinator.requestRandomWords(...);"
        ),
        "description": (
            "Miners/validators influence block.timestamp and block.difficulty "
            "within limits. Using them for randomness or sensitive thresholds "
            "is predictable/manipulable."
        ),
        "detection_heuristics": [
            "block.timestamp/block.number/blockhash used in randomness or critical thresholds"
        ],
        "references": ["https://swcregistry.io/docs/SWC-116"],
    },
    {
        "pattern_id": "evm:front-running-slippage",
        "title": "Front-running / missing slippage protection",
        "ecosystem": "ethereum",
        "category": "front-running",
        "swc": "SWC-114",
        "severity": "high",
        "vulnerable_code": (
            "function swap(uint256 amountIn) external {\n"
            "    uint256 out = getAmountOut(amountIn);\n"
            "    // no amountOutMin -> sandwich bots extract all margin\n"
            "    _swap(amountIn, out);\n"
            "}"
        ),
        "fixed_code": (
            "function swap(uint256 amountIn, uint256 amountOutMin, uint256 deadline) external {\n"
            '    require(block.timestamp <= deadline, "expired");\n'
            "    uint256 out = getAmountOut(amountIn);\n"
            '    require(out >= amountOutMin, "slippage too high");\n'
            "    _swap(amountIn, out);\n"
            "}"
        ),
        "description": (
            "MEV bots observe pending txs and sandwich them (buy before, sell "
            "after). Without amountOutMin/deadline parameters (or private "
            "order flow / commit-reveal), users are extracted."
        ),
        "detection_heuristics": [
            "swap functions without slippage params",
            "no deadline checks",
            "no private-mempool integration for high-value txs",
        ],
        "references": ["https://swcregistry.io/docs/SWC-114"],
    },
    {
        "pattern_id": "evm:donation-accounting",
        "title": "Accounting break via direct token donation",
        "ecosystem": "ethereum",
        "category": "business-logic",
        "swc": "SWC-109",
        "severity": "critical",
        "vulnerable_code": (
            "// Protocol tracks shares = token.balanceOf(this) / totalSupply.\n"
            "// An attacker donates tokens directly -> share price jumps, then\n"
            "// redeems the inflated value (Euler donate-to-self).\n"
            "function balanceUnderlying(address user) external view returns (uint256) {\n"
            "    return (token.balanceOf(address(this)) * sharesOf[user]) / totalShares;\n"
            "}"
        ),
        "fixed_code": (
            "// Track internal balance, not raw token.balanceOf(this); account\n"
            "// for donations with virtual shares / internal accounting; cap\n"
            "// first-depositor share inflation (min liquidity)."
        ),
        "description": (
            "Protocols that compute user value from token.balanceOf(this) are "
            "vulnerable to direct donations that inflate share price, enabling "
            "the attacker to withdraw more than deposited (Euler Finance $197M "
            "used donate-to-self plus flawed accounting)."
        ),
        "detection_heuristics": [
            "value computed as token.balanceOf(this) * shares / totalShares",
            "no minimum-liquidity / virtual-share protection on first deposit",
        ],
        "references": [
            "https://www.euler.finance/blog/euler-exploit-incident",
            "https://github.com/SunWeb3Sec/DeFiHackLabs",
        ],
    },
    {
        "pattern_id": "evm:upgrade-storage-collision",
        "title": "Storage collision in upgradeable contracts",
        "ecosystem": "ethereum",
        "category": "business-logic",
        "swc": "SWC-124",
        "severity": "high",
        "vulnerable_code": (
            "// V1: contract V1 { uint256 public owner; }  // slot 0\n"
            "// V2 adds: contract V2 { address public newOwner; uint256 public owner; }\n"
            "// newOwner collides with the old owner slot -> corrupted storage"
        ),
        "fixed_code": (
            "// Preserve storage layout across upgrades; use unstructured\n"
            "// storage (EIP-1967 slots) for proxy-critical values; add storage\n"
            "// gap fields in base contracts."
        ),
        "description": (
            "When an upgradeable contract changes storage layout, new variables "
            "can overlap old ones, corrupting state (openzeppelin ERC20 storage "
            "collision incidents, audited proxy pitfalls). Use EIP-1967 "
            "unstructured slots for implementation/owner and reserved gaps."
        ),
        "detection_heuristics": [
            "proxy upgrade changes variable order/types without migration",
            "missing __gap in upgradeable base contracts",
        ],
        "references": ["https://swcregistry.io/docs/SWC-124"],
    },
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
    # ── Solana ──────────────────────────────────────────────────────
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
    {
        "pattern_id": "sol:rent-exemption",
        "title": "Missing rent-exemption check (account lifecycle)",
        "ecosystem": "solana",
        "category": "input-validation",
        "swc": "",
        "severity": "medium",
        "vulnerable_code": (
            "// Account created without ensuring rent-exempt balance; if\n"
            "// lamports drop below rent, the account can be deleted/reaped\n"
            "// and state lost."
        ),
        "fixed_code": (
            "// Anchor: init / init_if_needed ensures rent exemption; or\n"
            "// require!(account.lamports() >= rent.minimum_balance(data_len));"
        ),
        "description": (
            "Non-rent-exempt accounts can be removed by the runtime, losing "
            "state. Create accounts with rent-exempt lamports (Anchor init "
            "handles this) and check lamports for externally-created accounts."
        ),
        "detection_heuristics": [
            "create_account without lamports >= minimum rent",
            "no rent-exemption check on mutable accounts",
        ],
        "references": ["https://docs.solana.com/developing/programming-model/accounts"],
    },
]

# ── SWC completeness fallbacks ─────────────────────────────────────────

# A short doc per SWC-100..136 (title + canonical vector/severity) so the
# contract_security collection has full SWC coverage even if the SWC registry
# fetch is rate-limited. Values come from rmi_rag.crypto_schema.
SWC_FALLBACK_TITLES: dict[str, str] = {
    "SWC-100": "Function Default Visibility",
    "SWC-101": "Integer Overflow and Underflow",
    "SWC-102": "Outdated Compiler Version",
    "SWC-103": "Floating Pragma",
    "SWC-104": "Unchecked Call Return Value",
    "SWC-105": "Unprotected Ether Withdrawal",
    "SWC-106": "Unprotected SELFDESTRUCT Instruction",
    "SWC-107": "Reentrancy",
    "SWC-108": "State Variable Default Visibility",
    "SWC-109": "Uninitialized Storage Pointer",
    "SWC-110": "Assert Violation",
    "SWC-111": "Use of Deprecated Solidity Functions",
    "SWC-112": "DELEGATECALL to Untrusted Callee",
    "SWC-113": "DoS with Failed Call",
    "SWC-114": "Transaction Order Dependence",
    "SWC-115": "Authorization through tx.origin",
    "SWC-116": "Block Values as a Proxy for Time",
    "SWC-117": "Signature Malleability",
    "SWC-118": "Incorrect Constructor Name",
    "SWC-119": "Shadowing State Variables",
    "SWC-120": "Weak Sources of Randomness from Chain Attributes",
    "SWC-121": "Missing Protection against Signature Replay Attacks",
    "SWC-122": "Missing Input Validation",
    "SWC-123": "Requirement Violation",
    "SWC-124": "Write to Arbitrary Storage Location",
    "SWC-125": "Incorrect Inheritance Order",
    "SWC-126": "Insufficient Gas Griefing",
    "SWC-127": "Arbitrary Jump with Function Type Variable",
    "SWC-128": "DoS With Block Gas Limit",
    "SWC-129": "Typographical Error",
    "SWC-130": "Right-To-Left-Override character U+202E",
    "SWC-131": "Presence of unused variables",
    "SWC-132": "Unexpected Ether balance",
    "SWC-133": "Hash Collisions With Multiple Variable Length Arguments",
    "SWC-134": "Message call with hardcoded gas amount",
    "SWC-135": "Code With No Effects",
    "SWC-136": "Unencrypted Private Data On-Chain",
}

# ── Real audit findings / exploit case studies ────────────────────────

AUDIT_FINDINGS: list[dict[str, Any]] = [
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
    {
        "finding_id": "audit:radiant-2024",
        "title": "Radiant Capital — rounding/accounting exploit ($58M)",
        "ecosystem": "ethereum",
        "category": "business-logic",
        "severity": "high",
        "pattern_class": "rounding / accounting precision in borrow accounting",
        "description": (
            "January 2024 (v2, BSC/ARB ~$4.5M) and October 2024 (~$58M): "
            "Radiant's lending pool accounting was exploited via rounding "
            "precision / inconsistent share accounting in the "
            "deposit/borrow path, allowing repeated mint/redemption of value."
        ),
        "detection_heuristics": [
            "share/accounting math with truncation that can be looped",
            "borrow/repay paths using inconsistent precision",
        ],
        "remediation": "Use high-precision internal accounting, virtual shares, and invariant tests over rounding.",
        "reference_url": "https://rekt.news/radiant-capital-rekt/",
        "amount_lost_usd": 58_000_000,
    },
    {
        "finding_id": "audit:eigen-2025",
        "title": "EigenLayer EIGEN — governance token theft ($5.5M)",
        "ecosystem": "ethereum",
        "category": "access-control",
        "severity": "high",
        "pattern_class": "governance token migration vulnerability",
        "description": (
            "April 2025: ~1.67M EIGEN (~$5.5M) was stolen from the EIGEN "
            "token contract via a vulnerability in the token's "
            "governance/migration logic, highlighting that even 'security "
            "primitives' ecosystems must audit token upgrade paths."
        ),
        "detection_heuristics": [
            "token migration/redeem functions with weak authorization",
            "upgradeable token logic reachable without timelock",
        ],
        "remediation": "Audit token upgrade/migration paths, gate privileged functions behind timelock + multisig.",
        "reference_url": "https://www.theblock.co/post/345761/eigenlayer-eigen-token-theft",
        "amount_lost_usd": 5_500_000,
    },
    {
        "finding_id": "audit:nomad-2022",
        "title": "Nomad bridge — trusted root optimization bug ($190M)",
        "ecosystem": "ethereum",
        "category": "input-validation",
        "severity": "critical",
        "pattern_class": "incorrect root initialization allowing message forgery",
        "description": (
            "August 2022: Nomad's trusted root was initialized to "
            "bytes32(0) (the zero hash), so any message with a zero-hashed "
            "root validated — enabling copy-paste message forgery that "
            "drained ~$190M within hours."
        ),
        "detection_heuristics": [
            "root/commitment initialized to zero and accepted in verification",
            "verification accepts default/empty values as valid",
        ],
        "remediation": "Never allow zero/empty commitments in verification; initialize trusted roots at deploy; add replay protection.",
        "reference_url": "https://rekt.news/nomad-rekt/",
        "amount_lost_usd": 190_000_000,
    },
]
