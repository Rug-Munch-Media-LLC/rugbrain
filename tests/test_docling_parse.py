"""Docling routing, structure preservation and fail-open behaviour.

Fixtures in ``tests/fixtures/docling/`` are real PDFs: ``table.pdf`` (table +
caption), ``multicolumn.pdf`` (interleaved two-column stream) and
``scanned.pdf`` (image-only page, no text operators). docling-serve itself is
mocked; the adapter contract is what is under test.
"""

from __future__ import annotations

import os
import pathlib

import httpx

from rugbrain.ingest_sources.docling_config import DoclingSettings, settings_from_env
from rugbrain.ingest_sources.docling_ingest import DocumentRequest, document_to_ingest_doc
from rugbrain.ingest_sources.document_parse import (
    parse_document,
    route_to_docling,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "docling"
URL = "http://docling.test"
ENDPOINT = f"{URL}/v1/convert/file"
SETTINGS = DoclingSettings(url=URL, enabled=True, max_bytes=5_000_000)

JSON_CONTENT = {
    "schema_name": "DoclingDocument",
    "version": "1.9.0",
    "pages": {"1": {"page_no": 1}},
    "texts": [
        {"label": "title", "text": "Liquidity Risk Assessment", "prov": [{"page_no": 1}]},
        {"label": "section_header", "text": "Reserves", "prov": [{"page_no": 1}]},
        {"label": "caption", "text": "Table 1: Reported reserves by chain",
         "prov": [{"page_no": 1}]},
    ],
    "tables": {"#/tables/0": {"label": "table", "data": {"num_rows": 4, "num_cols": 3}}},
}


def _payload(markdown: str, json_content: dict | None = None) -> dict:
    return {
        "document": {"md_content": markdown, "json_content": json_content or {}},
        "status": "success",
        "errors": [],
    }


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _mock(handler):
    """AsyncClient whose transport answers from *handler* (no network)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok(markdown: str, json_content: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1/convert/file")
        return httpx.Response(200, json=_payload(markdown, json_content))

    return handler


def test_fixtures_are_real_pdfs() -> None:
    for name in ("table.pdf", "multicolumn.pdf", "scanned.pdf"):
        assert _read(name).startswith(b"%PDF")


KEYS = ("DOCLING_URL", "DOCLING_ENABLED", "DOCLING_MAX_BYTES")


def test_settings_from_env() -> None:
    saved = {key: os.environ.get(key) for key in KEYS}
    try:
        os.environ.update(
            DOCLING_URL="http://docling:5001/", DOCLING_ENABLED="TRUE",
            DOCLING_MAX_BYTES="1234",
        )
        cfg = settings_from_env()
        assert cfg.usable and cfg.url == "http://docling:5001" and cfg.max_bytes == 1234
        os.environ["DOCLING_ENABLED"] = "0"
        assert not settings_from_env().usable
        os.environ["DOCLING_MAX_BYTES"] = "not-a-number"
        assert settings_from_env().max_bytes > 0
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_routing_matrix() -> None:
    data = b"%PDF-1.4"
    for name in ("a.pdf", "b.docx", "c.pptx", "d.xlsx", "e.html", "f.png"):
        assert route_to_docling(name, data, SETTINGS), name
    assert not route_to_docling("notes.md", b"# hi", SETTINGS)
    assert not route_to_docling("big.pdf", b"x" * 10, DoclingSettings(URL, True, 4))
    assert not route_to_docling("a.pdf", data, DoclingSettings(URL, False))


async def test_table_pdf_preserves_structure() -> None:
    md = "| Chain | TVL |\n| --- | --- |\n| Ethereum | 1200000 |"
    async with _mock(_ok(md, JSON_CONTENT)) as client:
        parsed = await parse_document(
            "table.pdf", _read("table.pdf"), settings=SETTINGS, client=client
        )
    assert parsed.parser == "docling" and parsed.parser_version == "1.9.0"
    assert parsed.metadata["tables"] == 1
    assert parsed.metadata["captions"] == ["Table 1: Reported reserves by chain"]
    assert parsed.metadata["headings"] == ["Liquidity Risk Assessment", "Reserves"]
    assert parsed.metadata["pages"] == 1 and parsed.metadata["page_numbers"] == [1]
    assert "| Ethereum | 1200000 |" in parsed.markdown


async def test_multicolumn_reading_order_is_kept() -> None:
    md = "Left column start.\nAttacker drained the vault.\nLeft column end.\n\nRight column start."
    async with _mock(_ok(md)) as client:
        parsed = await parse_document(
            "multicolumn.pdf", _read("multicolumn.pdf"), settings=SETTINGS, client=client
        )
    assert parsed.markdown.index("Left column end.") < parsed.markdown.index("Right column start.")
    assert parsed.parser == "docling"


async def test_scanned_page_requests_ocr() -> None:
    seen: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode("latin-1")
        return httpx.Response(200, json=_payload("Recovered OCR text.", {
            "schema_name": "DoclingDocument", "version": "1.9.0",
            "pages": {"1": {"page_no": 1}}, "texts": [],
        }))

    async with _mock(_capture) as client:
        parsed = await parse_document(
            "scanned.pdf", _read("scanned.pdf"), settings=SETTINGS, client=client
        )
    assert "do_ocr" in seen["body"]
    assert parsed.markdown == "Recovered OCR text."
    assert parsed.metadata["pages"] == 1 and parsed.metadata["headings"] == []


async def test_http_error_falls_back_to_markitdown() -> None:
    async with _mock(lambda request: httpx.Response(503)) as client:
        parsed = await parse_document(
            "table.pdf", _read("table.pdf"), settings=SETTINGS, client=client
        )
    assert parsed.parser == "markitdown"
    assert parsed.reason == "docling_http_503"
    assert "Chain" in parsed.markdown


async def test_timeout_falls_back() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    async with _mock(_boom) as client:
        parsed = await parse_document(
            "table.pdf", _read("table.pdf"), settings=SETTINGS, client=client
        )
    assert parsed.reason == "docling_timeout" and parsed.parser == "markitdown"


async def test_disabled_lane_uses_markitdown() -> None:
    parsed = await parse_document("table.pdf", _read("table.pdf"),
                                  settings=DoclingSettings(url=URL, enabled=False))
    assert parsed.parser == "markitdown" and parsed.reason == ""


async def test_size_cap_skips_docling() -> None:
    parsed = await parse_document("table.pdf", _read("table.pdf"),
                                  settings=DoclingSettings(URL, True, 10))
    assert parsed.parser == "markitdown" and parsed.reason == ""


async def test_empty_docling_output_falls_back() -> None:
    async with _mock(_ok("")) as client:
        parsed = await parse_document(
            "scanned.pdf", _read("scanned.pdf"), settings=SETTINGS, client=client
        )
    assert parsed.reason.startswith("docling_empty")


async def test_ingest_doc_carries_parser_provenance() -> None:
    async with _mock(_ok("Body", JSON_CONTENT)) as client:
        doc = await document_to_ingest_doc(
            DocumentRequest(
            source="crypto_whitepapers",
            doc_id="whitepapers:pdf:table.pdf",
            filename="table.pdf",
            data=_read("table.pdf"),
            content_type="application/pdf",
            title="Table paper",
            url="https://example.test/table.pdf",
            doc_type="whitepaper",
            collection="research_v2",
            settings=SETTINGS,
            client=client,
        )
    )
    assert doc.metadata["parser"] == "docling"
    assert doc.metadata["parser_version"] == "1.9.0"
    assert doc.metadata["tables"] == 1 and doc.metadata["collection"] == "research_v2"
    assert doc.source == "crypto_whitepapers" and doc.url.endswith("table.pdf")
