"""Schema contract: normalization, identity keys, fingerprints."""

from __future__ import annotations

from datetime import UTC, datetime

from rugbrain.zeroday.schema import (
    Record,
    iso_utc,
    parse_ts,
    severity_from_cvss,
    severity_from_text,
    slugify,
)


def test_severity_mapping() -> None:
    assert severity_from_cvss(9.8) == "critical"
    assert severity_from_cvss(7.1) == "high"
    assert severity_from_cvss(4.0) == "medium"
    assert severity_from_cvss(0.1) == "low"
    assert severity_from_cvss("nope") == "unknown"
    assert severity_from_text("MODERATE") == "medium"
    assert severity_from_text("CRITICAL") == "critical"
    assert severity_from_text("") == "unknown"


def test_timestamps_are_utc_and_tolerant() -> None:
    assert iso_utc("2026-10-25T10:00:00Z") == "2026-10-25T10:00:00Z"
    assert iso_utc(1_700_000_000) == "2023-11-14T22:13:20Z"
    assert iso_utc("not-a-date") is None
    assert parse_ts("2026-10-25").tzinfo is UTC
    assert slugify("ApeCoin / Governance!") == "apecoin-governance"


def test_ghsa_and_cve_collapse_to_one_identity() -> None:
    advisory = Record(
        source="github_advisories",
        kind="0day",
        title="Advisory",
        url="https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
        external_ids=("GHSA-aaaa-bbbb-cccc", "CVE-2024-1234"),
    )
    cve = Record(
        source="nvd",
        kind="0day",
        title="CVE record",
        url="https://nvd.nist.gov/vuln/detail/CVE-2024-1234",
        external_ids=("CVE-2024-1234",),
    )
    assert "cve:CVE-2024-1234" in advisory.alias_keys()
    assert "adv:GHSA-AAAA-BBBB-CCCC" in advisory.alias_keys()
    assert set(advisory.alias_keys()) & set(cve.alias_keys()) == {"cve:CVE-2024-1234"}


def test_incident_and_program_keys() -> None:
    exploit = Record(
        source="defillama_hacks",
        kind="exploit",
        title="ApeCoin exploit",
        url="https://defillama.com/hacks/2665",
        published_at="2022-03-17T00:00:00Z",
        protocol="ApeCoin",
    )
    assert "incident:apecoin:2022-03-17" in exploit.alias_keys()
    bounty = Record(
        source="immunefi",
        kind="bounty",
        title="Example",
        url="https://immunefi.com/bug-bounty/example/",
    )
    assert bounty.canonical_key() == "program:example"


def test_fingerprint_tracks_content_only() -> None:
    base = dict(source="nvd", kind="0day", title="T", url="https://x/1", severity="high")
    first = Record(**base)  # type: ignore[arg-type]
    second = Record(**{**base, "severity": "critical"})  # type: ignore[arg-type]
    assert first.fingerprint() != second.fingerprint()
    assert first.fingerprint() == Record(**base).fingerprint()  # type: ignore[arg-type]


def test_invalid_kind_is_rejected() -> None:
    try:
        Record(source="x", kind="rumour", title="t", url="https://x/1")
    except ValueError:
        return
    raise AssertionError("unknown kind must raise")


def test_datetime_bounds() -> None:
    now = datetime.now(UTC)
    assert parse_ts(now) == now
