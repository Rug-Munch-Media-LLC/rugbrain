"""Tests for the RugBrain fact-check gate (deterministic, no network)."""

from __future__ import annotations

from rugbrain.factcheck import (
    MIN_INDEPENDENT_SOURCES,
    bind_claims,
    check_draft,
    corroborate,
    extract_claims,
    verdict,
)

DRAFT = (
    "The attacker drained $12.4m from the protocol at "
    "0x1111111111111111111111111111111111111111, a 45% drop in TVL. "
    "A second wallet 0x2222222222222222222222222222222222222222 was involved."
)

# Two independent domains corroborate the first address + amount; only one
# domain mentions the second address (should flag).
SOURCES = [
    {
        "url": "https://reuters.com/story",
        "title": "Protocol drained",
        "content": "Attackers drained $12.4m from 0x1111111111111111111111111111111111111111.",
    },
    {
        "url": "https://coindesk.com/story",
        "title": "TVL falls",
        "content": "The protocol lost $12.4m; address 0x1111111111111111111111111111111111111111 flagged.",
    },
    {
        "url": "https://randomblog.example/post",
        "title": "Rumor",
        "content": "Wallet 0x2222222222222222222222222222222222222222 seen moving funds.",
    },
]


def test_extract_claims_finds_addresses_and_amounts():
    claims = extract_claims(DRAFT)
    keys = {c["key"] for c in claims}
    assert "0x1111111111111111111111111111111111111111" in keys
    assert "0x2222222222222222222222222222222222222222" in keys
    assert any(c["category"] == "amounts_usd" for c in claims)
    assert any(c["category"] == "percentages" for c in claims)


def test_extract_claims_deduplicates():
    claims = extract_claims("0xabc " * 3 + "0x" + "a" * 40)
    keys = [c["key"] for c in claims]
    assert len(keys) == len(set(keys))


def test_bind_claims_matches_source_text():
    claims = extract_claims(DRAFT)
    bound = bind_claims(claims, SOURCES)
    addr1 = next(c for c in bound if c["key"].endswith("1111"))
    assert len(addr1["sources"]) == 2
    assert {s["domain"] for s in addr1["sources"]} == {"reuters.com", "coindesk.com"}


def test_corroborate_requires_two_independent_domains():
    claims = extract_claims(DRAFT)
    bound = bind_claims(claims, SOURCES)
    addr1 = next(c for c in bound if c["key"].endswith("1111"))
    addr2 = next(c for c in bound if c["key"].endswith("2222"))
    assert corroborate(addr1)["corroborated"] is True
    assert corroborate(addr2)["corroborated"] is False
    assert MIN_INDEPENDENT_SOURCES == 2


def test_verdict_pass_and_flag():
    claims = extract_claims(DRAFT)
    bound = bind_claims(claims, SOURCES)
    addr1 = next(c for c in bound if c["key"].endswith("1111"))
    addr2 = next(c for c in bound if c["key"].endswith("2222"))
    assert verdict(corroborate(addr1))["verdict"] == "pass"
    assert verdict(corroborate(addr2))["verdict"] == "flag"


def test_check_draft_flags_uncorroborated_claim():
    report = check_draft(DRAFT, SOURCES)
    assert report["verdict"] == "flag"
    assert report["flagged"] >= 1
    assert report["passed"] >= 1
    assert report["claim_count"] == report["passed"] + report["flagged"]


def test_check_draft_passes_when_all_claims_corroborated():
    draft = "Drained $5m at 0x3333333333333333333333333333333333333333."
    sources = [
        {"url": "https://a.com/x", "content": "$5m 0x3333333333333333333333333333333333333333"},
        {"url": "https://b.com/y", "content": "$5m 0x3333333333333333333333333333333333333333"},
    ]
    report = check_draft(draft, sources)
    assert report["verdict"] == "pass"
    assert report["flagged"] == 0


def test_check_draft_no_claims_flags():
    report = check_draft("Just an opinion piece with no verifiable facts.", SOURCES)
    assert report["verdict"] == "flag"
    assert report["claim_count"] == 0


def test_same_domain_twice_is_not_independent():
    draft = "Lost $1m at 0x4444444444444444444444444444444444444444."
    sources = [
        {"url": "https://same.com/a", "content": "$1m 0x4444444444444444444444444444444444444444"},
        {"url": "https://same.com/b", "content": "$1m 0x4444444444444444444444444444444444444444"},
    ]
    report = check_draft(draft, sources)
    assert report["verdict"] == "flag"
