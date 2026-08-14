"""
Tests for scam_patterns.py — deterministic pattern extraction.

Verifies the aggregation of raw threat-intel docs into structured scam
patterns without any external services (pure function tests).
"""

from __future__ import annotations

from rugbrain.scam_patterns import (
    build_token_scam_profile,
    detect_attack_vectors,
    detect_indicators,
    extract_scam_patterns_from_docs,
)


def _doc(
    content: str,
    *,
    category: str = "reentrancy",
    attack_vector: str = "reentrancy",
    protocol: str = "Agave",
    chain: str = "EVM",
    severity: str = "critical",
    amount: float | str | None = 1_500_000,
    source_url: str = "https://github.com/SunWeb3Sec/DeFiHackLabs",
) -> dict:
    return {
        "content": content,
        "metadata": {
            "category": category,
            "attack_vector": attack_vector,
            "protocol": protocol,
            "contract_name": f"{protocol}Exploit",
            "chain": chain,
            "amount_lost_usd": amount,
            "source_url": source_url,
        },
        "severity": severity,
        "source": "defihacklabs",
    }


REENTRANCY_CODE = """
function withdraw(uint256 amount) external {
    require(balanceOf[msg.sender] >= amount);
    (bool ok, ) = msg.sender.call{value: amount}("");
    require(ok, "transfer failed");
    balanceOf[msg.sender] -= amount;
}
"""

FLASH_LOAN_CODE = """
function attack() external {
    uint256 amount = 1000 ether;
    ILendingPool pool = ILendingPool(0x...);
    pool.flashLoan(address(this), WETH, amount, abi.encode(""));
}
"""


def test_detect_attack_vectors_reentrancy():
    vectors = detect_attack_vectors(REENTRANCY_CODE)
    assert "reentrancy" in vectors


def test_detect_attack_vectors_flash_loan():
    vectors = detect_attack_vectors(FLASH_LOAN_CODE)
    assert "flash-loan" in vectors


def test_detect_attack_vectors_unknown_defaults():
    assert detect_attack_vectors("just a plain comment") == ["unknown"]


def test_detect_indicators_unchecked_external_call():
    indicators = detect_indicators(REENTRANCY_CODE)
    assert "reentrancy-guard-missing" in indicators
    assert "unchecked-external-call" in indicators


def test_detect_indicators_empty_code():
    assert detect_indicators("") == []
    assert detect_indicators(None) == []  # type: ignore[arg-type]


def test_extract_scam_patterns_groups_by_vector():
    docs = [
        _doc(REENTRANCY_CODE, protocol="Agave", attack_vector="reentrancy"),
        _doc(REENTRANCY_CODE, protocol="Another", attack_vector="reentrancy"),
        _doc(FLASH_LOAN_CODE, protocol="Aave", attack_vector="flash-loan"),
    ]
    patterns = extract_scam_patterns_from_docs(docs)

    # Should group into at least reentrancy + flash-loan patterns.
    vectors = {p["attack_type"] for p in patterns}
    assert "reentrancy" in vectors
    assert "flash-loan" in vectors

    reentrancy = next(p for p in patterns if p["attack_type"] == "reentrancy")
    assert "Agave" in reentrancy["affected_protocols"]
    assert "Another" in reentrancy["affected_protocols"]
    assert reentrancy["severity"] == "critical"
    assert reentrancy["funds_lost"] == 1_500_000.0
    assert reentrancy["doc_count"] == 2
    assert reentrancy["reference_incidents"]


def test_extract_scam_patterns_uses_metadata_when_vector_unknown():
    docs = [_doc(REENTRANCY_CODE, attack_vector="unknown", category="rug-pull")]
    patterns = extract_scam_patterns_from_docs(docs)
    # Content keyword detection should recover reentrancy.
    assert patterns[0]["attack_type"] == "reentrancy"


def test_extract_scam_patterns_string_amount():
    docs = [_doc(REENTRANCY_CODE, amount="$2.5M")]
    patterns = extract_scam_patterns_from_docs(docs)
    assert patterns[0]["funds_lost"] == 2_500_000.0


def test_extract_scam_patterns_empty():
    assert extract_scam_patterns_from_docs([]) == []


def test_extract_scam_patterns_severity_aggregation():
    docs = [
        _doc(REENTRANCY_CODE, severity="low"),
        _doc(REENTRANCY_CODE, severity="high"),
    ]
    patterns = extract_scam_patterns_from_docs(docs)
    assert patterns[0]["severity"] == "high"


def test_build_token_scam_profile_merges_nested_metadata():
    token = {
        "token_address": "0xabc",
        "chain": "bsc",
        "metadata": {
            "contract_code": "function mint() external onlyOwner {}",
            "sell_tax_pct": 15,
            "lp_locked": False,
        },
    }
    profile = build_token_scam_profile(token)
    assert profile["address"] == "0xabc"
    assert profile["chain"] == "bsc"
    assert profile["contract_code"] == "function mint() external onlyOwner {}"
    assert profile["economics"]["sell_tax_pct"] == 15
    assert profile["economics"]["lp_locked"] is False
