#!/usr/bin/env python3
"""Curated good-vs-bad contract dataset for ``ingest_good_bad_contracts.py``.

Part 3 of the contract-knowledge expansion: real-world-shaped Solidity/Rust
snippets that teach RugBrain what a secure implementation looks like
(``GOOD_CONTRACTS``) versus the patterns found in rug pulls and exploited
contracts (``BAD_CONTRACTS``).

Good contracts cover the canonical building blocks: secure ERC20,
ReentrancyGuard, CEI ordering, UUPS upgradeable proxy, timelock, multi-sig,
pausable, RBAC, plus Solana Anchor account validation and safe SPL close.

Bad contracts cover the classic scam families: honeypots, unlimited mints,
hidden backdoors, fake renounces, fee manipulation, blacklists, selfdestruct
drains, wallet-drainer approvals, and Solana Token-2022 transfer-hook /
permanent-delegate backdoors.
"""

from __future__ import annotations

from typing import Any

GOOD_CONTRACTS: list[dict[str, Any]] = [
    {
        "contract_id": "good:erc20-secure",
        "title": "Secure ERC20 (OpenZeppelin style)",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "good",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            'import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";\n'
            'import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";\n'
            "contract SecureToken is ERC20, Ownable {\n"
            "    uint256 public constant MAX_SUPPLY = 1_000_000_000e18;\n"
            '    constructor() ERC20("Secure", "SEC") Ownable(msg.sender) {\n'
            "        _mint(msg.sender, 100_000_000e18);  // fixed initial supply\n"
            "    }\n"
            "    function mint(address to, uint256 amount) external onlyOwner {\n"
            '        require(totalSupply() + amount <= MAX_SUPPLY, "cap exceeded");\n'
            "        _mint(to, amount);\n"
            "    }\n"
            "    function transferOwnership(address newOwner) public override onlyOwner {\n"
            '        require(newOwner != address(0), "zero owner");\n'
            "        super.transferOwnership(newOwner);\n"
            "    }\n"
            "}"
        ),
        "description": (
            "Canonical secure ERC20: fixed cap, owner-gated mint with an "
            "absolute supply cap, zero-address checks, renounceable ownership. "
            "This is the baseline every RugBrain comparison uses for 'good' "
            "token code."
        ),
        "best_practices": [
            "fixed total supply cap enforced in mint",
            "mint restricted to owner/role",
            "zero-address validation",
            "no hidden fee/blacklist logic",
        ],
    },
    {
        "contract_id": "good:reentrancy-guard",
        "title": "ReentrancyGuard + CEI (withdrawal)",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "good",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            'import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";\n'
            "contract Vault is ReentrancyGuard {\n"
            "    mapping(address => uint256) public balances;\n"
            "    function withdraw(uint256 amount) external nonReentrant {\n"
            '        require(balances[msg.sender] >= amount, "insufficient");\n'
            "        balances[msg.sender] -= amount;           // CEI: state first\n"
            '        (bool ok, ) = msg.sender.call{value: amount}("");\n'
            '        require(ok, "transfer failed");           // then interact\n'
            "    }\n"
            "}"
        ),
        "description": "The canonical CEI (checks-effects-interactions) withdrawal with a reentrancy guard.",
        "best_practices": [
            "CEI ordering",
            "nonReentrant on value-moving functions",
            "checked low-level call result",
        ],
    },
    {
        "contract_id": "good:uups-upgradeable",
        "title": "UUPS upgradeable proxy + initializer",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "good",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            'import {UUPSUpgradeable} from "@openzeppelin/contracts/proxy/utils/UUPSUpgradeable.sol";\n'
            'import {OwnableUpgradeable} from "@openzeppelin/contracts/access/OwnableUpgradeable.sol";\n'
            "contract Logic is Initializable, UUPSUpgradeable, OwnableUpgradeable {\n"
            "    uint256 public value;\n"
            "    /// @custom:oz-upgrades-unsafe-allow constructor\n"
            "    constructor() { _disableInitializers(); }\n"
            "    function initialize(address owner_) public initializer {\n"
            "        __Ownable_init(owner_);\n"
            "    }\n"
            "    function setValue(uint256 v) external onlyOwner { value = v; }\n"
            "    function _authorizeUpgrade(address) internal override onlyOwner {}\n"
            "}"
        ),
        "description": (
            "Secure UUPS proxy: constructor disables initializers (no "
            "front-running of initialize), _authorizeUpgrade gated to owner, "
            "EIP-1967 storage slots used by the library."
        ),
        "best_practices": [
            "_disableInitializers in constructor",
            "owner-gated _authorizeUpgrade",
            "EIP-1967 slots",
        ],
    },
    {
        "contract_id": "good:timelock",
        "title": "Timelock + owner for privileged actions",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "good",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            'import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";\n'
            "// Deploy: new TimelockController(minDelay, proposers, executors, admin)\n"
            "// Privileged functions must be callable only by the timelock, so\n"
            "// users get a delay to exit before parameters change."
        ),
        "description": (
            "TimelockController delays privileged operations (parameter "
            "changes, fee updates, upgrades) so users can react before a "
            "change is executed. Critical for high-value protocols."
        ),
        "best_practices": [
            "min delay > 0 for sensitive params",
            "role separation (proposer/executor)",
            "multisig as proposer",
        ],
    },
    {
        "contract_id": "good:multisig-wallet",
        "title": "Gnosis Safe-style multi-sig",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "good",
        "code": (
            "// Multi-sig: N-of-M owners must sign a transaction hash before\n"
            "// execution. Key property: threshold >= 2 and owners cannot\n"
            "// self-remove below threshold; nonces prevent replay.\n"
            "contract MultiSigWallet {\n"
            "    uint256 public threshold;\n"
            "    mapping(address => bool) public isOwner;\n"
            "    uint256 public nonce;\n"
            "    function execute(address to, uint256 value, bytes calldata data, bytes[] calldata sigs) external {\n"
            "        bytes32 txHash = keccak256(abi.encodePacked(to, value, data, nonce));\n"
            '        require(countValidSigs(txHash, sigs) >= threshold, "not enough sigs");\n'
            "        nonce++;\n"
            "        (bool ok, ) = to.call{value: value}(data);\n"
            '        require(ok, "exec failed");\n'
            "    }\n"
            "}"
        ),
        "description": "Threshold signatures prevent single-owner compromise from controlling funds (see Ronin bridge).",
        "best_practices": [
            "threshold >= 2",
            "nonce against replay",
            "owner set managed by the wallet itself",
        ],
    },
    {
        "contract_id": "good:pausable",
        "title": "Pausable emergency stop",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "good",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            'import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";\n'
            "contract Vault is Pausable {\n"
            "    function withdraw(uint256 amount) external whenNotPaused { ... }\n"
            "    function pause() external onlyOwner { _pause(); }\n"
            "    function unpause() external onlyOwner { _unpause(); }\n"
            "}"
        ),
        "description": "Pausable lets a trusted role halt value-moving operations during an incident, buying time for investigation.",
        "best_practices": [
            "whenNotPaused on critical functions",
            "pause owner-gated",
            "unpause after remediation",
        ],
    },
    {
        "contract_id": "good:access-control-rbac",
        "title": "AccessControl RBAC (roles not single owner)",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "good",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            'import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";\n'
            "contract Protocol is AccessControl {\n"
            '    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");\n'
            '    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");\n'
            "    function mint(address to, uint256 amount) external onlyRole(MINTER_ROLE) { ... }\n"
            "}"
        ),
        "description": "Role-based access control lets you rotate/disassociate roles instead of a single owner key controlling everything.",
        "best_practices": [
            "separate roles per capability",
            "role admin rotation",
            "timelock for role grants",
        ],
    },
    {
        "contract_id": "good:solana-anchor-validation",
        "title": "Solana Anchor account validation (safe pattern)",
        "ecosystem": "solana",
        "language": "rust",
        "verdict": "good",
        "code": (
            "use anchor_lang::prelude::*;\n"
            "#[derive(Accounts)]\n"
            "pub struct Deposit<'info> {\n"
            "    #[account(mut)]\n"
            "    pub user: Signer<'info>,\n"
            "    #[account(\n"
            "        init,\n"
            "        payer = user,\n"
            "        space = 8 + VaultState::INIT_SPACE,\n"
            '        seeds = [b"vault", user.key().as_ref()],\n'
            "        bump\n"
            "    )]\n"
            "    pub vault: Account<'info, VaultState>,\n"
            "    pub system_program: Program<'info, System>,\n"
            "}\n"
            "#[account]\n"
            "pub struct VaultState {\n"
            "    pub owner: Pubkey,\n"
            "    pub total_deposited: u64,\n"
            "}\n"
            "pub fn deposit(ctx: Context<Deposit>, amount: u64) -> Result<()> {\n"
            "    ctx.accounts.vault.total_deposited = ctx.accounts.vault.total_deposited\n"
            "        .checked_add(amount).ok_or(ErrorCode::Overflow)?;\n"
            "    Ok(())\n"
            "}"
        ),
        "description": (
            "Anchor's account constraints give you signer, PDA-seed+bump, "
            "owner, and rent checks for free. This is the canonical 'good' "
            "Solana pattern."
        ),
        "best_practices": [
            "Signer for authorities",
            "seeds+bump PDA constraints",
            "checked arithmetic",
            "init ensures rent exemption",
        ],
    },
    {
        "contract_id": "good:solana-token-close-safe",
        "title": "Solana safe token account close",
        "ecosystem": "solana",
        "language": "rust",
        "verdict": "good",
        "code": (
            "use anchor_spl::token::{self, Token, TokenAccount};\n"
            "#[derive(Accounts)]\n"
            "pub struct CloseAccount<'info> {\n"
            "    #[account(mut)]\n"
            "    pub authority: Signer<'info>,\n"
            "    #[account(\n"
            "        mut,\n"
            "        close = authority,\n"
            "        constraint = token_account.owner == authority.key() @ ErrorCode::NotOwner,\n"
            "        constraint = token_account.mint == mint.key() @ ErrorCode::WrongMint\n"
            "    )]\n"
            "    pub token_account: Account<'info, TokenAccount>,\n"
            "    pub mint: Account<'info, Mint>,\n"
            "    pub token_program: Program<'info, Token>,\n"
            "}"
        ),
        "description": (
            "Safe close pattern: Anchor's close= returns rent to the "
            "authority and zeroes the account, with mint/owner constraints "
            "preventing cross-account confusion."
        ),
        "best_practices": [
            "close = destination for lamport recovery",
            "mint/owner constraints",
            "verify before close",
        ],
    },
]

BAD_CONTRACTS: list[dict[str, Any]] = [
    {
        "contract_id": "bad:honeypot-sell-locked",
        "title": "Honeypot — sell locked to everyone except owner",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "contract HoneyPot is ERC20 {\n"
            "    mapping(address => bool) public canSell;\n"
            "    function _update(address from, address to, uint256 amount) internal override {\n"
            "        if (from != address(0) && to != address(0)) {  // transfers only\n"
            '            require(canSell[from] || from == owner, "sell locked");\n'
            "        }\n"
            "        super._update(from, to, amount);\n"
            "    }\n"
            "    // owner never sets canSell[anyone] = true -> buyers can never sell\n"
            "}"
        ),
        "description": (
            "Classic honeypot: buyers can buy (via the DEX pair) but the "
            "token blocks all sells except from the owner. The deployer "
            "dumps into the trapped liquidity."
        ),
        "red_flags": [
            "sell/transfer blocked except for owner",
            "only owner can sell",
            "buy tax 0 / sell tax 100%",
            "blacklist all non-owner from selling",
        ],
    },
    {
        "contract_id": "bad:unlimited-mint",
        "title": "Rug pull — owner unlimited mint",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "contract RugMint is ERC20 {\n"
            "    address public owner;\n"
            '    modifier onlyOwner() { require(msg.sender == owner, "no"); _; }\n'
            "    function mint(address to, uint256 amount) external onlyOwner {\n"
            "        _mint(to, amount);  // no cap, no pause — owner mints and dumps\n"
            "    }\n"
            "}"
        ),
        "description": "Uncapped owner-only mint: deployer dilutes holders to zero then sells. No supply cap, no renounce.",
        "red_flags": [
            "mint() without cap",
            "onlyOwner mint callable anytime",
            "totalSupply unbounded",
            "ownership never renounced",
        ],
    },
    {
        "contract_id": "bad:hidden-backdoor",
        "title": "Hidden backdoor — admin sweep of any token",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "contract Backdoored {\n"
            "    address public owner;\n"
            "    function sweep(address token, address to) external {\n"
            "        require(msg.sender == owner, \"no\");  // 'owner' can seize everything\n"
            "        IERC20(token).transfer(to, IERC20(token).balanceOf(address(this)));\n"
            "    }\n"
            "}"
        ),
        "description": "A 'sweep'/'rescue' function lets the owner transfer out any token balance — including user funds held by the contract.",
        "red_flags": [
            "sweep/rescue/emergencyWithdraw transferring user funds",
            "owner can move arbitrary token balances",
            "no timelock on sweep",
        ],
    },
    {
        "contract_id": "bad:fake-renounce",
        "title": "Fake renounce — ownership to dead address but proxy keeps owner",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// The token 'renounces' by transferring ownership to 0xdead...\n"
            "// BUT the contract is upgradeable: the proxy admin/upgrader is a\n"
            "// separate address still controlled by the deployer.\n"
            "// Or: renounceOwnership() is overridden to be a no-op while the UI\n"
            "// claims the deployer burned the keys."
        ),
        "description": "Fake renounce scams claim decentralization while the deployer retains a proxy admin, a hidden owner slot, or an overridden renounce.",
        "red_flags": [
            "ownership to 0xdead but proxy admin intact",
            "renounceOwnership overridden/no-op",
            "hidden owner in upgradeable implementation",
            "secondary admin role",
        ],
    },
    {
        "contract_id": "bad:fee-manipulation",
        "title": "Fee manipulation — owner changes buy/sell tax to 100%",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "contract FeeRug is ERC20 {\n"
            "    uint256 public buyTaxBps = 100;   // 1%\n"
            "    uint256 public sellTaxBps = 100;\n"
            "    function setTax(uint256 buy, uint256 sell) external {\n"
            '        require(msg.sender == owner, "no");\n'
            "        buyTaxBps = buy; sellTaxBps = sell;  // owner sets sell tax to 10000 -> rug\n"
            "    }\n"
            "}"
        ),
        "description": "Owner-controlled tax parameters without a cap or timelock let the deployer set the sell tax to 100%, trapping holders.",
        "red_flags": [
            "tax settable to 100%",
            "no max-tax bound",
            "tax changes without timelock",
            "fee sent to deployer wallet",
        ],
    },
    {
        "contract_id": "bad:blacklist-everyone",
        "title": "Blacklist — owner excludes any holder from selling",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "contract Blacklister is ERC20 {\n"
            "    mapping(address => bool) public blacklisted;\n"
            "    function setBlacklisted(address a, bool v) external {\n"
            '        require(msg.sender == owner, "no");\n'
            "        blacklisted[a] = v;\n"
            "    }\n"
            "    function _update(address from, address to, uint256 amount) internal override {\n"
            '        require(!blacklisted[from] && !blacklisted[to], "blocked");\n'
            "        super._update(from, to, amount);\n"
            "    }\n"
            "}"
        ),
        "description": "Owner blacklists any holder (e.g. everyone who buys), freezing their balance while the deployer dumps.",
        "red_flags": [
            "owner-controlled blacklist on transfers",
            "blacklist applied to non-owner holders",
            "no timelock/notice for blacklists",
        ],
    },
    {
        "contract_id": "bad:selfdestruct-drain",
        "title": "Selfdestruct drain",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// SPDX-License-Identifier: MIT\n"
            "pragma solidity ^0.8.20;\n"
            "contract Drainable {\n"
            "    address public owner;\n"
            "    function kill() external {\n"
            '        require(msg.sender == owner, "no");\n'
            "        selfdestruct(payable(owner));  // sweeps ALL ETH in the contract\n"
            "    }\n"
            "}"
        ),
        "description": "An owner-callable selfdestruct immediately transfers the contract's entire ETH balance to the deployer.",
        "red_flags": ["selfdestruct present", "selfdestruct owner-callable", "no timelock"],
    },
    {
        "contract_id": "bad:wallet-drainer-approve",
        "title": "Wallet drainer — unlimited approve + transferFrom sweep",
        "ecosystem": "ethereum",
        "language": "solidity",
        "verdict": "bad",
        "code": (
            "// Victim signs an unlimited approval (or a malicious permit).\n"
            "// The drainer then calls transferFrom repeatedly to sweep\n"
            "// every approved asset from the wallet in one tx.\n"
            "function drain(address victim, address[] calldata tokens) external {\n"
            "    for (uint256 i = 0; i < tokens.length; i++) {\n"
            "        uint256 bal = IERC20(tokens[i]).balanceOf(victim);\n"
            "        IERC20(tokens[i]).transferFrom(victim, address(this), bal);\n"
            "    }\n"
            "}"
        ),
        "description": "The core of phishing wallet drainers (Inferno/Pink Drainer): one approval + one tx sweeps all assets.",
        "red_flags": [
            "request for type(uint256).max approval",
            "contract immediately transferFrom after approve",
            "permit phishing signatures",
        ],
    },
    {
        "contract_id": "bad:solana-transfer-hook-drainer",
        "title": "Solana Token-2022 transfer-hook drainer",
        "ecosystem": "solana",
        "language": "rust",
        "verdict": "bad",
        "code": (
            "// Token-2022 mint with a TransferHook extension. The hook\n"
            "// program executes on EVERY transfer and can siphon a cut or\n"
            "// block transfers unless extra accounts are passed.\n"
            "pub fn transfer_hook(ctx: Context<TransferHook>) -> Result<()> {\n"
            "    // attacker's hook: charge tax, or fail unless 'allowed' list\n"
            "    transfer_tokens(&ctx, amount * 99 / 100)?;  // steal 1%\n"
            "    Ok(())\n"
            "}"
        ),
        "description": (
            "Token-2022 transfer hooks run arbitrary code on every transfer. "
            "Malicious mints use them to tax or block sells, and to trick "
            "wallets into signing hooks they don't understand."
        ),
        "red_flags": [
            "mint with transfer_hook extension from unknown program",
            "hook charges fee not shown in UI",
            "hook blocks transfers unless extra accounts",
        ],
    },
    {
        "contract_id": "bad:solana-permanent-delegate",
        "title": "Solana permanent delegate backdoor",
        "ecosystem": "solana",
        "language": "rust",
        "verdict": "bad",
        "code": (
            "// Token-2022 PermanentDelegate extension: the delegate can\n"
            "// transfer/withdraw tokens from ANY holder WITHOUT approval.\n"
            "// If the deployer set themselves as permanent delegate, they\n"
            "// can drain every holder at any time.\n"
            "let delegate = get_permanent_delegate(&mint)?;  // deployer address\n"
            "token::transfer(delegate, attacker, amount, ...)?;"
        ),
        "description": (
            "Permanent delegate is a legitimate Token-2022 feature (for "
            "recovery) but a backdoor when controlled by the deployer: it "
            "allows withdrawing any holder's balance without approval."
        ),
        "red_flags": [
            "permanent delegate != none",
            "permanent delegate is deployer/EOA",
            "no on-chain disclosure",
        ],
    },
    {
        "contract_id": "bad:solana-hidden-mint-authority",
        "title": "Solana hidden mint authority",
        "ecosystem": "solana",
        "language": "rust",
        "verdict": "bad",
        "code": (
            "// UI says 'mint authority revoked', but the mint's authority\n"
            "// is a PDA owned by a program that still lets the deployer\n"
            "// mint — or the authority is set to a burn address that the\n"
            "// deployer holds the private key for.\n"
            "let mint_authority = mint.mint_authority;  // still Some(...) -> can mint"
        ),
        "description": "Hidden mint authority: supply can still be inflated via a PDA program or a 'burned' key the deployer controls.",
        "red_flags": [
            "mint_authority is Some(...) (not revoked)",
            "authority is a PDA with owner program",
            "authority 'burned' but key known to deployer",
        ],
    },
]
