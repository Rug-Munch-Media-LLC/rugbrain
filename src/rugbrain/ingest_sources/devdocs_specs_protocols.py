"""Declared devdocs, group: protocol specifications (x402, MCP).

The two protocols this fleet builds against and neither of which is in the stack-docs
lane: x402 is the payment gate on the product surface, MCP is the tool surface every agent
speaks. Both are specs plus SDKs, because the spec says what is required and the SDK
README says what the released client actually does — those diverge, and the divergence is
what breaks an integration.

DEAD PATHS ARE REMOVED, NOT LEFT TO 404. The first bounded run (2026-10-25) found
``specs/x402-specification.md``, ``SPEC.md`` and ``python/README.md`` absent from the
repo at ``main``; they are not declared here any more, so a refresh reports a real
regression instead of a permanent, ignorable failure line.

OVERLAP IS A DEFECT. Check the stack-docs lane before adding a family here: a page that is
already retrievable twice is noise competing with itself in a top-k cut.

See ``devdocs_schema`` for the field contract, ``devdocs`` for the fetcher, and
``devdocs_specs`` for the aggregate every consumer reads.
"""

from __future__ import annotations

from typing import Final

from rugbrain.ingest_sources.devdocs_schema import (
    DOCS_COLLECTION,
    LICENCE_APACHE2,
    LICENCE_MIT,
    DocSite,
)

_X402: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="x402_spec",
        family="x402",
        url="https://github.com/coinbase/x402",
        licence=LICENCE_APACHE2,
        doc_type="protocol_spec",
        collection=DOCS_COLLECTION,
        urls=(
            "https://raw.githubusercontent.com/coinbase/x402/main/README.md",
        ),
        cap=1,
    ),
    DocSite(
        name="x402_sdk",
        family="x402",
        url="https://github.com/coinbase/x402",
        licence=LICENCE_APACHE2,
        doc_type="sdk_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://raw.githubusercontent.com/coinbase/x402/main/typescript/README.md",
            "https://raw.githubusercontent.com/coinbase/x402/main/go/README.md",
        ),
        cap=2,
    ),
)

_MCP: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="mcp_spec",
        family="mcp",
        url="https://modelcontextprotocol.io/specification",
        licence=LICENCE_MIT,
        doc_type="protocol_spec",
        collection=DOCS_COLLECTION,
        urls=(
            "https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/README.md",
            "https://modelcontextprotocol.io/specification/2025-06-18",
            "https://modelcontextprotocol.io/specification/2025-06-18/server/tools",
        ),
        cap=3,
    ),
    DocSite(
        name="mcp_servers",
        family="mcp",
        url="https://github.com/modelcontextprotocol/servers",
        licence=LICENCE_MIT,
        doc_type="sdk_docs",
        collection=DOCS_COLLECTION,
        urls=("https://raw.githubusercontent.com/modelcontextprotocol/servers/main/README.md",),
        cap=1,
    ),
)

#: Protocol specs + SDK docs, in fetch order.
PROTOCOL_SITES: Final[tuple[DocSite, ...]] = _X402 + _MCP

__all__ = ["PROTOCOL_SITES"]
