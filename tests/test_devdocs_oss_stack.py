"""Tests for the OSS-stack devdocs group and its coverage register.

Three layers, matching the existing devdocs test file:

  1. provenance — every declared site carries a URL, a licence token and a bounded cap
  2. uniqueness — no duplicate family and no duplicate page across the whole manifest
  3. register    — the coverage matrix cannot claim a tool the fetcher has never heard of
"""

from __future__ import annotations

import pytest

from rugbrain.ingest_sources.devdocs_schema import DOCS_COLLECTION
from rugbrain.ingest_sources.devdocs_specs import DOC_SITES
from rugbrain.ingest_sources.devdocs_specs_oss_stack import OSS_STACK_SITES
from scripts.check_oss_stack_coverage import PROBES, TOOLS, problems, render_markdown

EXPECTED_FAMILIES = {
    "markitdown",
    "docling",
    "openmetadata",
    "metabase",
    "grafana",
    "prometheus",
    "ruff",
    "valkey",
}


def test_group_covers_the_audited_gap() -> None:
    assert {site.family for site in OSS_STACK_SITES} == EXPECTED_FAMILIES


def test_every_site_carries_provenance_and_a_bounded_cap() -> None:
    for site in OSS_STACK_SITES:
        assert site.url.startswith("https://"), site.name
        assert site.licence and site.doc_type == "tool_docs", site.name
        assert site.collection == DOCS_COLLECTION, site.name
        assert 1 <= site.cap <= 3, f"{site.name}: cap {site.cap} is not bounded"
        assert 1 <= len(site.urls) <= site.cap, site.name
        assert all(url.startswith("https://") for url in site.urls), site.name


def test_group_is_wired_into_the_aggregate() -> None:
    for site in OSS_STACK_SITES:
        assert site in DOC_SITES, f"{site.name} is declared but never fetched"


def test_no_duplicate_pages_in_the_manifest() -> None:
    urls = [url for site in DOC_SITES for url in site.urls]
    assert len(urls) == len(set(urls)), "a declared page appears twice"


def test_register_has_no_lying_rows() -> None:
    assert problems() == []


def test_register_catches_a_declared_tool_with_no_family() -> None:
    broken = (*TOOLS, ("pulsar", "devdocs", "declared", "Apache-2.0", "README.md"))
    assert any("pulsar" in line for line in problems(broken))


def test_register_catches_a_blocked_entry_without_a_reason() -> None:
    broken = (*TOOLS, ("sketchy", "docs", "blocked", "MIT", "dead"))
    assert any("blocked needs a reason" in line for line in problems(broken))


def test_markdown_matrix_renders_every_tool() -> None:
    rendered = render_markdown()
    for name, *_ in TOOLS:
        assert f"`{name}`" in rendered
    for _, tool in PROBES:
        assert f"`{tool}`" in rendered


@pytest.mark.parametrize("probe,tool", PROBES)
def test_every_probe_points_at_a_registered_tool(probe: str, tool: str) -> None:
    assert tool in {row[0] for row in TOOLS}, probe
