"""
Tests for contract_analysis.py — the Part 4 contract-security pipeline.

Covers the deterministic, pure parts (static heuristics, source parsing,
assessment aggregation) so they can be verified without live Qdrant/Qwen3.
"""

from __future__ import annotations

from rugbrain.contract_analysis import (
    CHAIN_IDS,
    _aggregate_assessment,
    _etherscan_sources_to_text,
    _sourcify_sources_to_text,
    static_security_scan,
)

REENTRANCY_BAD = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
contract Vault {
    mapping(address => uint256) public balances;
    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "insufficient");
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        balances[msg.sender] -= amount;
    }
}
"""

REENTRANCY_GOOD = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
contract Vault is ReentrancyGuard {
    mapping(address => uint256) public balances;
    function withdraw(uint256 amount) external nonReentrant {
        require(balances[msg.sender] >= amount, "insufficient");
        balances[msg.sender] -= amount;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
    }
}
"""

TX_ORIGIN = """
contract Gate {
    address public owner;
    function privileged() external {
        require(tx.origin == owner, "not owner");
        // ...
    }
}
"""

SECURE_ERC20 = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
contract SecureToken is ERC20, Ownable {
    uint256 public constant MAX_SUPPLY = 1_000_000_000e18;
    constructor() ERC20("Secure", "SEC") Ownable(msg.sender) {
        _mint(msg.sender, 100_000_000e18);
    }
    function mint(address to, uint256 amount) external onlyOwner {
        require(totalSupply() + amount <= MAX_SUPPLY, "cap exceeded");
        _mint(to, amount);
    }
}
"""


def _severities(findings):
    return {f["rule"]: f["severity"] for f in findings}


def test_static_scan_reentrancy_bad_order_flagged():
    findings = static_security_scan(REENTRANCY_BAD)
    rules = _severities(findings)
    assert rules.get("reentrancy-unsafe-order") == "high"


def test_static_scan_cei_order_not_flagged_high():
    findings = static_security_scan(REENTRANCY_GOOD)
    rules = _severities(findings)
    assert rules.get("reentrancy-unsafe-order") is None
    # raw external call should still surface at most as info
    assert rules.get("raw-external-call", "info") == "info"


def test_static_scan_tx_origin():
    findings = static_security_scan(TX_ORIGIN)
    assert _severities(findings).get("tx-origin-auth") == "critical"


def test_static_scan_selfdestruct_and_delegatecall():
    findings = static_security_scan(
        "function kill() external { selfdestruct(payable(owner)); }"
        "function exec(address t, bytes calldata d) external { t.delegatecall(d); }"
    )
    rules = _severities(findings)
    assert rules.get("selfdestruct") == "critical"
    assert rules.get("delegatecall") == "high"


def test_static_scan_secure_erc20_no_critical():
    findings = static_security_scan(SECURE_ERC20)
    critical = [f for f in findings if f["severity"] == "critical"]
    assert critical == []
    rules = _severities(findings)
    # owner-gated mint should not be flagged as unrestricted
    assert rules.get("unrestricted-mint") is None


def test_static_scan_unrestricted_mint_flagged():
    findings = static_security_scan(
        "function mint(address to, uint256 amount) external { _mint(to, amount); }"
    )
    rules = _severities(findings)
    assert rules.get("unrestricted-mint") == "high"


def test_static_scan_empty_source():
    assert static_security_scan("") == []


def test_sourcify_metadata_parsing():
    metadata = {
        "compiler": {"version": "0.8.20+commit.a1b79de6"},
        "sources": {
            "contracts/Token.sol": {"content": "pragma solidity ^0.8.20; contract Token {}"},
            "contracts/Other.sol": {"content": "contract Other {}"},
        },
    }
    combined, name, version = _sourcify_sources_to_text(metadata)
    assert "Token.sol" in combined
    assert "contract Token {}" in combined
    assert name == "Token"
    assert version == "0.8.20+commit.a1b79de6"


def test_etherscan_single_file_parsing():
    row = {
        "ContractName": "Simple",
        "CompilerVersion": "0.8.20",
        "SourceCode": "pragma solidity ^0.8.20; contract Simple {}",
    }
    combined, name, version = _etherscan_sources_to_text(row)
    assert name == "Simple"
    assert "contract Simple {}" in combined
    assert version == "0.8.20"


def test_etherscan_multi_file_parsing():
    row = {
        "ContractName": "Wrapper",
        "CompilerVersion": "0.8.20",
        "SourceCode": '{{"sources": {"A.sol": {"content": "contract A {}"}, "B.sol": {"content": "contract B {}"}}}}',
    }
    combined, name, _version = _etherscan_sources_to_text(row)
    assert "A.sol" in combined
    assert "B.sol" in combined
    assert name == "Wrapper"


def test_aggregate_assessment_high_on_bad_match():
    assessment, _ = _aggregate_assessment(
        static=[],
        similar_good=[],
        similar_bad=[{"similarity": 0.75}],
        vuln_matches=[],
    )
    assert assessment == "high"


def test_aggregate_assessment_high_on_critical_static():
    assessment, _ = _aggregate_assessment(
        static=[{"severity": "critical"}],
        similar_good=[],
        similar_bad=[],
        vuln_matches=[],
    )
    assert assessment == "high"


def test_aggregate_assessment_medium_on_medium_static():
    assessment, _ = _aggregate_assessment(
        static=[{"severity": "medium"}],
        similar_good=[],
        similar_bad=[],
        vuln_matches=[],
    )
    assert assessment == "medium"


def test_aggregate_assessment_info_when_clean():
    assessment, summary = _aggregate_assessment([], [], [], [])
    assert assessment == "info"
    assert summary  # non-empty summary string


def test_chain_ids_cover_major_chains():
    assert CHAIN_IDS["ethereum"] == 1
    assert CHAIN_IDS["bsc"] == 56
    assert CHAIN_IDS["polygon"] == 137
    assert CHAIN_IDS["arbitrum"] == 42161
    assert CHAIN_IDS["base"] == 8453
