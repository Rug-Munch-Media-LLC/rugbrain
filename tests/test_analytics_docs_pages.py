"""Unit tests for the analytics-docs pure parsers.

The sectioning rules protect API tables and code examples from the fixed
2,000-character chunker downstream, so the fence and table assertions below are
behavioural, not cosmetic: a fence split across two points means a code example
that cannot be retrieved whole.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.ingest_sources.analytics_docs_pages import (
    is_page_url,
    parse_llms_txt,
    parse_sitemap,
    section_doc_id,
    split_sections,
)


def test_parse_llms_txt_absolutizes_and_dedupes() -> None:
    text = "- [Intro](/intro.md)\n- [Intro again](/intro.md)\n- [API](https://x.io/api.md)\n"
    urls = parse_llms_txt(text, "https://docs.example.io")
    assert urls == ["https://docs.example.io/intro.md", "https://x.io/api.md"]


def test_parse_sitemap_separates_nested_and_skips_assets() -> None:
    xml = "<urlset><loc>https://d.io/a</loc><loc>https://d.io/b.xml</loc><loc>https://d.io/x.png</loc></urlset>"
    pages, nested = parse_sitemap(xml)
    assert pages == ["https://d.io/a"]
    assert nested == ["https://d.io/b.xml"]


def test_is_page_url_rejects_assets_and_honours_prefix() -> None:
    assert not is_page_url("https://d.io/logo.svg")
    assert is_page_url("https://d.io/api/v1/accounts")
    assert not is_page_url("https://d.io/api/v1/accounts", include=("/docs",))


def test_split_sections_keeps_fenced_hash_comments() -> None:
    text = (
        "# Intro\n\n" + "a" * 300 + "\n\n"
        "## Api\n\n" + "b" * 300 + "\n\n"
        "```bash\n# this is a shell comment\necho hi\n```\n"
    )
    sections = split_sections(text)
    headings = [h for h, _ in sections]
    assert headings == ["Intro", "Api"]
    assert "# this is a shell comment" in sections[1][1]


def test_split_sections_merges_short_stub_into_previous() -> None:
    text = "# Intro\n\n" + "a" * 300 + "\n\n## Parameters\n\n`id`\n"
    sections = split_sections(text)
    assert len(sections) == 1
    assert "`id`" in sections[0][1]


def test_split_sections_hard_splits_oversized_section() -> None:
    text = "# Big\n\n" + "x" * 9000
    sections = split_sections(text, max_chars=6000)
    assert len(sections) >= 2
    assert sections[1][0].endswith("(continued)")


def test_section_doc_id_is_deterministic_and_content_addressed() -> None:
    a = section_doc_id("etherscan", "https://docs.etherscan.io/api", 2, "body")
    b = section_doc_id("etherscan", "https://docs.etherscan.io/api", 2, "body")
    c = section_doc_id("etherscan", "https://docs.etherscan.io/api", 2, "other")
    assert a == b
    assert a != c
    assert a.startswith("analytics_docs:etherscan:")
