"""Chunk 4 of ``SECURITY_PATTERNS`` (generated; literal data only).

Source: scripts/contract_knowledge_data.py
"""

ITEMS = [
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
]
