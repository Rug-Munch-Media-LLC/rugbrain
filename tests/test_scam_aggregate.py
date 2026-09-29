"""Unit tests for rugbrain.scam_aggregate — docs -> grouped scam patterns.

Split out of `scam_patterns.py` on 2026-09-29 (size ratchet). The grouping key
and the sort order are what a user sees, so both are pinned here.
"""

from __future__ import annotations

from typing import Any

from rugbrain.scam_aggregate import empty_group, extract_scam_patterns_from_docs


def _doc(
    content: str,
    *,
    attack_vector: str = "reentrancy",
    category: str = "reentrancy",
    protocol: str = "Agave",
    chain: str = "EVM",
    severity: str = "critical",
    amount: Any = 1_500_000,
    source_url: str = "https://example.test/poc",
) -> dict[str, Any]:
    return {
        "content": content,
        "severity": severity,
        "metadata": {
            "attack_vector": attack_vector,
            "category": category,
            "protocol": protocol,
            "chain": chain,
            "amount_lost_usd": amount,
            "source_url": source_url,
        },
    }


class TestGrouping:
    def test_same_vector_and_technique_group_together(self) -> None:
        docs = [
            _doc("reentrancy call.value withdraw()", protocol="A"),
            _doc("reentrancy call.value sweep", protocol="B"),
        ]
        patterns = extract_scam_patterns_from_docs(docs)
        assert len(patterns) == 1
        assert patterns[0]["doc_count"] == 2
        assert set(patterns[0]["affected_protocols"]) == {"A", "B"}

    def test_technique_comes_from_the_first_indicator(self) -> None:
        patterns = extract_scam_patterns_from_docs([_doc("reentrancy call.value withdraw()")])
        assert patterns[0]["technique"] == "reentrancy-guard-missing"
        assert patterns[0]["pattern_id"] == "reentrancy:reentrancy-guard-missing"

    def test_technique_falls_back_to_the_vector(self) -> None:
        patterns = extract_scam_patterns_from_docs([_doc("governance proposal vote quorum")])
        assert patterns[0]["technique"] == patterns[0]["attack_type"]

    def test_empty_input_is_empty_output(self) -> None:
        assert extract_scam_patterns_from_docs([]) == []

    def test_doc_without_metadata_does_not_crash(self) -> None:
        patterns = extract_scam_patterns_from_docs(
            [{"content": "reentrancy call.value", "metadata": {}}]
        )
        assert len(patterns) == 1
        assert patterns[0]["affected_protocols"] == []
        assert patterns[0]["chain"] == ""


class TestMerging:
    def test_longest_snippet_wins(self) -> None:
        short = _doc("reentrancy call.value short", protocol="A")
        long = _doc("reentrancy call.value " + "x" * 200, protocol="A")
        patterns = extract_scam_patterns_from_docs([short, long])
        assert len(patterns[0]["code_snippet"]) == len(long["content"])

    def test_funds_lost_takes_the_maximum(self) -> None:
        docs = [
            _doc("reentrancy call.value", amount=10, protocol="A"),
            _doc("reentrancy call.value", amount=99, protocol="A"),
        ]
        assert extract_scam_patterns_from_docs(docs)[0]["funds_lost"] == 99.0

    def test_severity_takes_the_maximum(self) -> None:
        docs = [
            _doc("reentrancy call.value", severity="low", protocol="A"),
            _doc("reentrancy call.value", severity="critical", protocol="A"),
        ]
        assert extract_scam_patterns_from_docs(docs)[0]["severity"] == "critical"

    def test_reference_incidents_are_deduplicated(self) -> None:
        docs = [
            _doc("reentrancy call.value", protocol="A"),
            _doc("reentrancy call.value", protocol="A"),
        ]
        assert extract_scam_patterns_from_docs(docs)[0]["reference_incidents"] == [
            "https://example.test/poc"
        ]

    def test_indicators_are_deduplicated(self) -> None:
        docs = [
            _doc("reentrancy call.value withdraw()", protocol="A"),
            _doc("reentrancy call.value sweep()", protocol="A"),
        ]
        indicators = extract_scam_patterns_from_docs(docs)[0]["indicators"]
        assert len(indicators) == len(set(indicators))


class TestSortOrder:
    def test_severity_outranks_funds(self) -> None:
        # Severity is the primary key. A critical 1.5M loss outranks a high
        # 2.5bn one -- the documented order, not a funds-first ranking.
        docs = [
            _doc(
                "reentrancy call.value",
                severity="high",
                amount="2.5 billion",
                attack_vector="reentrancy",
                category="reentrancy",
                protocol="A",
            ),
            _doc(
                "governance proposal vote quorum",
                severity="critical",
                amount=1_500_000,
                attack_vector="governance-attack",
                category="governance-attack",
                protocol="B",
            ),
        ]
        patterns = extract_scam_patterns_from_docs(docs)
        assert [p["severity"] for p in patterns] == ["critical", "high"]

    def test_equal_severity_sorts_by_funds_descending(self) -> None:
        docs = [
            _doc(
                "reentrancy call.value",
                severity="high",
                amount=10,
                attack_vector="reentrancy",
                category="reentrancy",
                protocol="A",
            ),
            _doc(
                "governance proposal vote quorum",
                severity="high",
                amount=999,
                attack_vector="governance-attack",
                category="governance-attack",
                protocol="B",
            ),
        ]
        patterns = extract_scam_patterns_from_docs(docs)
        assert [p["funds_lost"] for p in patterns] == [999.0, 10.0]


class TestEmptyGroup:
    def test_starts_at_the_documented_defaults(self) -> None:
        group = empty_group("reentrancy", "withdraw-after-call")
        assert group["pattern_id"] == "reentrancy:withdraw-after-call"
        assert group["severity"] == "info"
        assert group["funds_lost"] == 0.0
        assert group["doc_count"] == 0
        assert group["affected_protocols"] == []

    def test_mutable_fields_are_per_call(self) -> None:
        # Two groups must not share the same list object.
        a, b = empty_group("x", "y"), empty_group("x", "z")
        a["indicators"].append("leak")
        assert b["indicators"] == []
