"""Whitepaper PDFs go through the Docling/markitdown adapter, not a stub."""

from __future__ import annotations

import pathlib

import httpx

from rugbrain.ingest_sources import whitepapers
from rugbrain.ingest_sources.docling_config import DoclingSettings

PDF = (pathlib.Path(__file__).parent / "fixtures" / "docling" / "table.pdf").read_bytes()
SETTINGS = DoclingSettings(url="http://docling.test", enabled=True)


def _transport(deliver: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "raw.githubusercontent.com" in str(request.url):
            return httpx.Response(200, content=deliver)
        return httpx.Response(
            200,
            json={
                "document": {
                    "md_content": "Table 1: Reserves\n\n| Chain | TVL |\n| --- | --- |\n"
                    "| Ethereum | 1200000 |\nReserves fell 12% quarter over quarter in the "
                    "attestation, which the foundation published for every chain in scope.",
                    "json_content": {
                        "schema_name": "DoclingDocument",
                        "version": "1.9.0",
                        "pages": {"1": {"page_no": 1}},
                        "texts": [{"label": "caption", "text": "Table 1: Reserves",
                                   "prov": [{"page_no": 1}]}],
                        "tables": {"#/tables/0": {"label": "table"}},
                    },
                },
                "status": "success",
            },
        )

    return httpx.MockTransport(handler)


async def test_pdf_whitepaper_uses_docling_when_enabled() -> None:
    async with httpx.AsyncClient(transport=_transport(PDF)) as client:
        doc = await whitepapers._pdf_doc(client, "ethereum.pdf", len(PDF), SETTINGS)
    assert doc.metadata["parser"] == "docling"
    assert doc.metadata["parser_version"] == "1.9.0"
    assert doc.metadata["tables"] == 1 and doc.metadata["format"] == "pdf"
    assert "Ethereum" in doc.content and doc.title == "ethereum"


async def test_unreadable_pdf_keeps_index_stub() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        doc = await whitepapers._pdf_doc(client, "missing.pdf", 1234, SETTINGS)
    assert doc.metadata["pending_extraction"] is True
    assert doc.metadata["format"] == "pdf"
