"""
Tests for pattern_matching.py — the pattern matching engine.

Covers bytecode similarity, deployer-behavior rules, token-economics rules,
and the full orchestrator. Pure-function tests (no external services).
"""

from __future__ import annotations

from rmi_rag.pattern_matching import (
    match_bytecode,
    match_deployer_behavior,
    match_token_economics,
    match_token_patterns,
    score_scam_risk,
)

REENTRANCY_PATTERN = {
    "pattern_id": "reentrancy:withdraw-after-external-call",
    "attack_type": "reentrancy",
    "technique": "withdraw-after-external-call",
    "code_snippet": 'function withdraw(uint256 amount) external { (bool ok, ) = msg.sender.call{value: amount}(""); balanceOf[msg.sender] -= amount; }',
    "indicators": ["reentrancy-guard-missing", "unchecked-external-call"],
    "severity": "critical",
    "reference_incidents": ["https://example.com/rekt"],
}

SAME_CODE = (
    "function withdraw(uint256 amount) external { "
    '(bool ok, ) = msg.sender.call{value: amount}(""); '
    "balanceOf[msg.sender] -= amount; }"
)


def test_match_bytecode_identical_code_matches():
    matches = match_bytecode(SAME_CODE, [REENTRANCY_PATTERN])
    assert len(matches) == 1
    assert matches[0]["name"] == "reentrancy"
    assert matches[0]["confidence"] >= 0.4
    assert matches[0]["reference_incident"] == "https://example.com/rekt"


def test_match_bytecode_unrelated_code_no_match():
    code = "contract PlainToken { string public name; function setName(string memory n) public { name = n; } }"
    matches = match_bytecode(code, [REENTRANCY_PATTERN])
    assert matches == []


def test_match_bytecode_empty_code():
    assert match_bytecode("", [REENTRANCY_PATTERN]) == []
    assert match_bytecode("short", [REENTRANCY_PATTERN]) == []


def test_match_deployer_behavior_serial_deployer():
    signals = match_deployer_behavior({"deployment_count": 5})
    assert any(s["name"] == "serial-deployer" for s in signals)


def test_match_deployer_behavior_mixer_funded():
    signals = match_deployer_behavior({"mixer_funded": True})
    assert any(s["name"] == "mixer-funded-deployer" for s in signals)


def test_match_deployer_behavior_rapid_multi_chain():
    signals = match_deployer_behavior({"rapid_deploy": True, "multi_chain": True})
    names = {s["name"] for s in signals}
    assert "rapid-deploy-after-rug" in names
    assert "multi-chain-launch" in names


def test_match_deployer_behavior_clean_no_signals():
    assert match_deployer_behavior({}) == []


def test_match_token_economics_high_sell_tax():
    signals = match_token_economics({"sell_tax_pct": 20})
    assert any(s["name"] == "high-sell-tax" for s in signals)


def test_match_token_economics_unlocked_lp_mint():
    signals = match_token_economics({"lp_locked": False, "mint_enabled": True})
    names = {s["name"] for s in signals}
    assert "unlocked-liquidity" in names
    assert "mint-enabled" in names


def test_match_token_economics_concentration():
    signals = match_token_economics({"holder_concentration_pct": 60})
    assert any(s["name"] == "holder-concentration" for s in signals)


def test_match_token_economics_clean_no_signals():
    assert match_token_economics({"lp_locked": True, "ownership_renounced": True}) == []


async def test_match_token_patterns_full_pipeline():
    token = {
        "address": "0xabc",
        "chain": "bsc",
        "metadata": {
            "contract_code": SAME_CODE,
            "deployer_address": "0xdef",
            "deployment_count": 6,
            "mixer_funded": True,
            "sell_tax_pct": 25,
            "lp_locked": False,
            "mint_enabled": True,
        },
    }
    result = await match_token_patterns(token, patterns=[REENTRANCY_PATTERN])
    matched = result["matched_patterns"]
    names = {m["name"] for m in matched}

    assert "reentrancy" in names
    assert "serial-deployer" in names
    assert "mixer-funded-deployer" in names
    assert "high-sell-tax" in names
    assert "unlocked-liquidity" in names
    assert "mint-enabled" in names

    assert "risk_signals" in result
    assert len(result["risk_signals"]) > 0


async def test_match_token_patterns_clean_token():
    token = {
        "address": "0xabc",
        "chain": "ethereum",
        "metadata": {
            "contract_code": "contract Safe { string name; }",
            "lp_locked": True,
            "ownership_renounced": True,
        },
    }
    result = await match_token_patterns(token, patterns=[REENTRANCY_PATTERN])
    assert result["matched_patterns"] == []


def test_score_scam_risk_unknown_when_empty():
    assert score_scam_risk([], [])["risk"] == "unknown"


def test_score_scam_risk_high():
    matches = [
        {
            "name": "mixer-funded-deployer",
            "confidence": 0.85,
            "severity": "critical",
            "technique": "deployer-behavior",
        }
    ]
    verdict = score_scam_risk(matches, ["mixer-funded-deployer"])
    assert verdict["risk"] == "high"
    assert verdict["top_pattern"] == "mixer-funded-deployer"


def test_score_scam_risk_medium():
    # Two moderate signals → medium (single 0.35 signal alone is low).
    matches = [
        {
            "name": "fresh-deployment",
            "confidence": 0.35,
            "severity": "medium",
            "technique": "deployer-behavior",
        },
        {
            "name": "unlocked-liquidity",
            "confidence": 0.7,
            "severity": "critical",
            "technique": "token-economics",
        },
    ]
    verdict = score_scam_risk(matches, ["fresh-deployment", "unlocked-liquidity"])
    assert verdict["risk"] == "medium"
