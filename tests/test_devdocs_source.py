"""Tests for the devdocs / engineering-blog ingest sources.

Three layers, per ``docs/ADDING-A-SOURCE.md`` §Step 5:

  1. registry  — both sources are registered with the right doc_type/collection
  2. parser    — the pure normalizers (``page_to_text``, ``doc_from_page``) behave
  3. fetch     — ``collect`` / ``collect_feeds`` fetch, dedupe and fail open

No network: every HTTP call is mocked with ``respx``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from rugbrain.ingest_sources import get_source, list_sources
from rugbrain.ingest_sources.devdocs import collect
from rugbrain.ingest_sources.devdocs_page import doc_from_page, page_to_text
from rugbrain.ingest_sources.devdocs_specs import DOC_SITES, DocSite
from rugbrain.ingest_sources.eng_blogs import collect_feeds

RICH_HTML = (
    "<html><body><nav>menu</nav><main><h1>Asyncio Tasks</h1>"
    + ("task text " * 80)
    + "</main></body></html>"
)
RICH_RAW = "# Functions\n\n" + ("A rust function item has a body. " * 40)
THIN = "<html><body><main>404</main></body></html>"

SITE = DocSite(
    name="test_site",
    family="python",
    url="https://example.test/docs",
    licence="PSF-2.0",
    doc_type="language_docs",
    collection="rag_documents_8b",
    urls=("https://example.test/a", "https://example.test/b"),
    cap=2,
)


def test_sources_registered() -> None:
    names = list_sources()
    assert "devdocs_static" in names
    assert "eng_blogs" in names
    static = get_source("devdocs_static")
    blogs = get_source("eng_blogs")
    assert static is not None and blogs is not None
    assert static.default_collection == "rag_documents_8b"
    assert blogs.default_collection == "research_v2_8b"


def test_manifest_has_no_duplicate_urls() -> None:
    urls = [url for site in DOC_SITES for url in site.urls]
    assert len(urls) == len(set(urls)), "a declared page appears twice"
    names = [site.name for site in DOC_SITES]
    assert len(names) == len(set(names)), "a source name appears twice"


def test_page_to_text_strips_html_but_keeps_prose() -> None:
    text = page_to_text(RICH_HTML, is_html=True)
    assert "menu" not in text
    assert "Asyncio Tasks" in text
    assert "<" not in text


def test_page_to_text_passes_raw_markdown_through() -> None:
    text = page_to_text(RICH_RAW, is_html=False)
    assert text.startswith("# Functions")
    assert "rust function item" in text


def test_doc_from_page_is_deterministic_and_carries_provenance() -> None:
    first = doc_from_page(SITE, "https://example.test/a", RICH_HTML, is_html=True)
    second = doc_from_page(SITE, "https://example.test/a", RICH_HTML, is_html=True)
    assert first is not None and second is not None
    assert first.doc_id == second.doc_id
    assert first.metadata["licence"] == "PSF-2.0"
    assert first.metadata["canonical_url"] == "https://example.test/docs"
    assert first.metadata["collection"] == "rag_documents_8b"
    assert first.doc_type == "language_docs"


def test_doc_from_page_refuses_thin_pages() -> None:
    assert doc_from_page(SITE, "https://example.test/a", THIN, is_html=True) is None


@pytest.mark.asyncio
@respx.mock
async def test_collect_dedupes_and_fails_open() -> None:
    respx.get("https://example.test/a").mock(return_value=httpx.Response(200, text=RICH_HTML))
    respx.get("https://example.test/b").mock(return_value=httpx.Response(200, text=RICH_HTML))
    async with httpx.AsyncClient() as client:
        docs, stats = await collect(client, sites=(SITE,))
    assert stats["requested"] == 2
    assert stats["emitted"] == 1, "identical pages must collapse to one document"
    assert stats["duplicates"] == 1
    assert len(docs) == 1


@pytest.mark.asyncio
@respx.mock
async def test_collect_records_http_failure() -> None:
    respx.get("https://example.test/a").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/b").mock(return_value=httpx.Response(200, text=RICH_RAW))
    async with httpx.AsyncClient() as client:
        docs, stats = await collect(client, sites=(SITE,))
    assert stats["failed"] == 1
    assert stats["emitted"] == 1
    assert stats["failures"][0]["status"] == 404
    assert docs[0].doc_type == "language_docs"


FEED_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>PBS ordering</title><link>https://example.test/pbs</link>
<description>why relay ordering matters</description>
<pubDate>Mon, 01 Jan 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


@pytest.mark.asyncio
@respx.mock
async def test_collect_feeds_emits_docs_with_licence() -> None:
    feed_site = DocSite(
        name="test_feed",
        family="flashbots",
        url="https://example.test",
        licence="CC-BY-4.0",
        doc_type="engineering_blog",
        collection="research_v2_8b",
        urls=("https://example.test/rss.xml",),
        cap=5,
    )
    respx.get("https://example.test/rss.xml").mock(return_value=httpx.Response(200, text=FEED_XML))
    async with httpx.AsyncClient() as client:
        docs, stats = await collect_feeds(client, sites=(feed_site,))
    assert stats["emitted"] >= 1
    assert docs[0].metadata["collection"] == "research_v2_8b"
    assert docs[0].metadata["licence"] == "CC-BY-4.0"
    assert docs[0].doc_type == "engineering_blog"
