"""Source adapters: pure parsers against captured upstream shapes."""

from __future__ import annotations

from rugbrain.zeroday.feeds import parse_feed, within_days
from rugbrain.zeroday.sources_bounty import _anchors
from rugbrain.zeroday.sources_flow import _hack, _loss_severity
from rugbrain.zeroday.sources_osv import _osv
from rugbrain.zeroday.sources_vuln import _ghsa, _kev, _nvd

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>PoC: Router drain</title><link>https://example.test/poc/1</link>
<pubDate>Sat, 25 Oct 2026 08:00:00 +0000</pubDate>
<description>demo</description></item></channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atomic item</title><link href="https://example.test/a/2"/>
<published>2026-10-20T00:00:00Z</published><summary>s</summary></entry></feed>"""


def test_parse_feed_handles_rss_and_atom() -> None:
    rss = parse_feed(RSS)
    assert rss[0]["link"] == "https://example.test/poc/1"
    assert rss[0]["published"].startswith("Sat, 25 Oct 2026")

    atom = parse_feed(ATOM)
    assert atom[0]["title"] == "Atomic item"
    assert atom[0]["link"] == "https://example.test/a/2"

    assert parse_feed("<rss><channel>") == []


def test_window_filter() -> None:
    assert within_days("2026-10-24T00:00:00Z", 30) is True
    assert within_days("2020-01-01T00:00:00Z", 30) is False
    assert within_days(None, 30) is False


def test_ghsa_normalization() -> None:
    record = _ghsa(
        {
            "ghsa_id": "GHSA-aaaa-bbbb-cccc",
            "cve_id": "CVE-2026-1234",
            "summary": "Prototype pollution",
            "severity": "high",
            "published_at": "2026-10-01T00:00:00Z",
            "html_url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
            "cvss": {"score": 8.1},
            "references": [{"url": "https://github.com/x/poc"}],
            "vulnerabilities": [
                {
                    "vulnerable_version_range": "< 2.0.0",
                    "first_patched_version": {"identifier": "2.0.0"},
                }
            ],
        }
    )
    assert record is not None
    assert record.severity == "high"
    assert record.fixed_version == "2.0.0"
    assert record.poc_url == "https://github.com/x/poc"
    assert "CVE-2026-1234" in record.external_ids


def test_nvd_and_osv_and_kev_normalization() -> None:
    nvd = _nvd(
        {
            "cve": {
                "id": "CVE-2026-5555",
                "published": "2026-10-02T00:00:00Z",
                "descriptions": [{"lang": "en", "value": "RPC crash"}],
                "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8}}]},
                "references": [{"url": "https://nvd.test/1"}],
            }
        }
    )
    assert nvd is not None and nvd.severity == "critical"

    osv = _osv(
        {
            "id": "GHSA-zzzz-yyyy-xxxx",
            "aliases": ["CVE-2026-7777"],
            "summary": "DoS",
            "published": "2026-10-03T00:00:00Z",
            "severity": [
                {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}
            ],
            "affected": [
                {
                    "package": {"name": "pkg", "ecosystem": "npm"},
                    "versions": ["1.0.0"],
                    "ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.0.1"}]}],
                }
            ],
        }
    )
    assert osv is not None and osv.fixed_version == "1.0.1"

    kev = _kev(
        {
            "cveID": "CVE-2026-0001",
            "vulnerabilityName": "Exploited node bug",
            "dateAdded": "2026-10-20",
            "vendorProject": "Example",
            "knownRansomwareCampaignUse": "Known",
        }
    )
    assert kev is not None and kev.severity == "critical"


def test_defillama_hack_normalization() -> None:
    record = _hack(
        {
            "date": 1_700_000_000,
            "name": "ApeCoin",
            "classification": "Governance",
            "technique": "Flashloan Governance Attack",
            "amount": 820000,
            "chain": ["Ethereum"],
            "defillamaId": "2665",
        }
    )
    assert record is not None
    assert record.chain == "Ethereum"
    assert record.loss_usd == 820000
    assert record.severity == "medium"
    assert _loss_severity("bad") == "unknown"


def test_bounty_anchor_extraction() -> None:
    page = """
    <a href="/bug-bounty/example-protocol/"><span>Example Protocol</span></a>
    <a href="/bug-bounty/page-abcdef">next</a>
    <a href="/other/thing">Other</a>
    """
    links = _anchors(page, "/bug-bounty/", "https://immunefi.com")
    assert links == [("https://immunefi.com/bug-bounty/example-protocol/", "Example Protocol")]
