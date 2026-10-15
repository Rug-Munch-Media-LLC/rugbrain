"""Tests for the deployer -> token linkage projection.

Rows are Forta-shaped, taken from the field list the source module documents
(``contract_address``, ``contract_creator``, ``malicious``, ``contract_tag``,
``contract_name``). The point of these tests is the RISK RULE and the
attribution boundary: a deployer with no attributed contracts must read
``unknown``, never ``low``.
"""

from __future__ import annotations

from rugbrain.deployer_linkage import (
    CRITICAL_MALICIOUS,
    FACTORY_THRESHOLD,
    deployer_risk,
    link_deployer_tokens,
    linkage_to_docs,
)

D1 = "0x1111111111111111111111111111111111111111"
D2 = "0x2222222222222222222222222222222222222222"
T1 = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
T2 = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
T3 = "0xcccccccccccccccccccccccccccccccccccccccc"


def _row(creator: str, address: str, malicious: bool, **extra: object) -> dict[str, object]:
    return {
        "contract_creator": creator,
        "contract_address": address,
        "malicious": malicious,
        **extra,
    }


# ── grouping ────────────────────────────────────────────────────────────


def test_groups_contracts_by_creator() -> None:
    rows = [
        _row(D1, T1, True),
        _row(D1, T2, False),
        _row(D2, T3, True),
    ]
    linkage = link_deployer_tokens(rows)
    assert set(linkage) == {D1, D2}
    assert linkage[D1]["total_count"] == 2
    assert linkage[D1]["malicious_count"] == 1
    assert linkage[D1]["benign_count"] == 1
    assert linkage[D2]["total_count"] == 1


def test_creator_address_is_lowercased() -> None:
    linkage = link_deployer_tokens([_row(D1.upper(), T1.upper(), True)])
    assert D1 in linkage
    assert linkage[D1]["tokens"][0]["address"] == T1


def test_duplicate_contract_counts_once() -> None:
    """The same contract appearing twice is one contract, not two."""
    rows = [_row(D1, T1, True), _row(D1, T1, True)]
    linkage = link_deployer_tokens(rows)
    assert linkage[D1]["total_count"] == 1
    assert linkage[D1]["malicious_count"] == 1


def test_rows_without_creator_or_address_are_skipped() -> None:
    rows = [
        _row("", T1, True),
        _row(D1, "", True),
        {"malicious": True},
        "not-a-dict",  # type: ignore[list-item]
    ]
    assert link_deployer_tokens(rows) == {}


# ── the risk rule ───────────────────────────────────────────────────────


def test_no_contracts_is_unknown_not_low() -> None:
    """'never seen this deployer' must not be published as 'clean'."""
    assert deployer_risk({"total_count": 0, "malicious_count": 0}) == "unknown"


def test_one_malicious_is_medium() -> None:
    assert deployer_risk({"total_count": 1, "malicious_count": 1}) == "medium"


def test_two_malicious_is_high() -> None:
    assert deployer_risk({"total_count": 2, "malicious_count": 2}) == "high"


def test_three_malicious_is_critical() -> None:
    assert CRITICAL_MALICIOUS == 3
    assert deployer_risk({"total_count": 3, "malicious_count": 3}) == "critical"


def test_one_malicious_from_a_factory_is_critical() -> None:
    """A single malicious contract from a prolific deployer is a factory pattern."""
    assert FACTORY_THRESHOLD == 5
    assert deployer_risk({"total_count": 5, "malicious_count": 1}) == "critical"
    assert deployer_risk({"total_count": 4, "malicious_count": 1}) == "medium"


def test_benign_only_is_low() -> None:
    assert deployer_risk({"total_count": 3, "malicious_count": 0}) == "low"


def test_risk_is_stamped_on_every_entry() -> None:
    linkage = link_deployer_tokens([_row(D1, T1, True), _row(D1, T2, True)])
    assert linkage[D1]["risk"] == "high"


# ── doc emission ────────────────────────────────────────────────────────


def test_linkage_to_docs_is_keyed_on_the_deployer() -> None:
    linkage = link_deployer_tokens([_row(D1, T1, True), _row(D1, T2, False)])
    docs = linkage_to_docs(linkage)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.doc_id == f"forta_deployer_linkage:{D1}"
    assert doc.metadata["deployer"] == D1
    assert doc.metadata["deployer_risk"] == "medium"
    assert doc.metadata["deployer_malicious_count"] == 1
    assert doc.metadata["deployer_total_count"] == 2
    assert set(doc.metadata["deployer_token_addresses"]) == {T1, T2}
    assert doc.metadata["collection"] == "scam_signatures"


def test_doc_id_is_deterministic_across_runs() -> None:
    rows = [_row(D1, T1, True)]
    first = linkage_to_docs(link_deployer_tokens(rows))[0].doc_id
    second = linkage_to_docs(link_deployer_tokens(rows))[0].doc_id
    assert first == second


def test_doc_content_names_the_verdict_and_the_tokens() -> None:
    linkage = link_deployer_tokens([_row(D1, T1, True, contract_name="RugToken")])
    content = linkage_to_docs(linkage)[0].content
    assert "1 labelled malicious" in content
    assert T1 in content
    assert "MALICIOUS" in content
    assert "RugToken" in content


def test_empty_linkage_emits_nothing() -> None:
    assert linkage_to_docs({}) == []
