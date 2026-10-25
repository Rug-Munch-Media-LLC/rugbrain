"""Declared devdocs, group: the OSS tools we run that had NO documentation ingest.

The audit of 2026-10-25 asked ContextCrypto seven representative questions about our own
stack (``zoekt query syntax``, ``serena activate_project``, ``metamcp namespace``,
``ClickHouse TTL``, ``Qdrant hybrid``, ``LiteLLM fallbacks``, ``opencode mcp.servers``).
All seven answered — but only because those sources were already loaded. A second pass
against the 247 ``docs:`` libraries in the corpus found eight tools the fleet operates
with ZERO documentation points: MarkItDown, Docling, OpenMetadata, Metabase, Grafana,
Prometheus, Ruff and Valkey.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT
    This is the repair for exactly those eight. It is not a second declaration of the
    ``docs:`` lane: a tool that already answers (serena, zoekt, metamcp, clickhouse,
    qdrant, redis, neo4j, litellm, repomix, docker-compose, fastapi, pydantic, sqlalchemy,
    pytest) is recorded as ``resync`` in ``scripts/check_oss_stack_coverage.py`` and gets
    a docs-lane resync, never a duplicate family here. Duplicating a family forks
    retrieval for no gain, so it counts as a defect, not as coverage.

WHY THESE PAGES AND NOT THE SITES
    Each entry points at the file that states the contract — a ``README.md`` or the raw
    configuration reference — because raw ``.md``/``.ini`` arrives clean and the section
    chunker can split it on real headings. A Grafana *provisioning* page and a Prometheus
    ``configuration.md`` (217 KB of directive reference) beat fifty marketing pages: the
    failure mode with an operator tool is a field name, not an overview.

LICENCES ARE READ, NOT ASSUMED: every value came from the repository's own licence via a
GitHub probe on 2026-10-25. Metabase reports NOASSERTION there; its LICENSE file is
AGPL-3.0, so it is declared as such. Both AGPL trees stay identifiable so a
product-bound pass can see them without re-deriving.

See ``devdocs_schema`` for the field contract and ``devdocs_specs`` for the aggregate.
"""

from __future__ import annotations

from typing import Final

from rugbrain.ingest_sources.devdocs_schema import (
    DOCS_COLLECTION,
    LICENCE_AGPL3,
    LICENCE_APACHE2,
    LICENCE_BSD3,
    LICENCE_MIT,
    DocSite,
)

_RAW = "https://raw.githubusercontent.com"

#: Document-extraction tools: what turns a PDF/DOCX/HTML into the text we ingest.
_DOC_TOOLS: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="markitdown_docs",
        family="markitdown",
        url="https://github.com/microsoft/markitdown",
        licence=LICENCE_MIT,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(f"{_RAW}/microsoft/markitdown/main/README.md",),
        cap=1,
    ),
    DocSite(
        name="docling_docs",
        family="docling",
        url="https://github.com/docling-project/docling",
        licence=LICENCE_MIT,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(f"{_RAW}/docling-project/docling/main/README.md",),
        cap=1,
    ),
)

#: Catalogs and dashboards we operate: config shape, not the product tour.
_BI_TOOLS: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="openmetadata_docs",
        family="openmetadata",
        url="https://docs.open-metadata.org/",
        licence=LICENCE_APACHE2,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/open-metadata/OpenMetadata/main/README.md",
            "https://docs.open-metadata.org/latest/deployment/docker",
        ),
        cap=2,
    ),
    DocSite(
        name="metabase_docs",
        family="metabase",
        url="https://www.metabase.com/docs/",
        licence=LICENCE_AGPL3,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/metabase/metabase/master/README.md",
            "https://www.metabase.com/docs/latest/configuring-metabase/config-file",
        ),
        cap=2,
    ),
    DocSite(
        name="grafana_docs",
        family="grafana",
        url="https://grafana.com/docs/grafana/latest/",
        licence=LICENCE_AGPL3,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/grafana/grafana/main/README.md",
            f"{_RAW}/grafana/grafana/main/conf/defaults.ini",
            # JS-rendered HTML page: too thin after the tag strip, so the raw source wins.
            f"{_RAW}/grafana/grafana/main/docs/sources/administration/provisioning/index.md",
        ),
        cap=3,
    ),
    DocSite(
        name="prometheus_docs",
        family="prometheus",
        url="https://prometheus.io/docs/",
        licence=LICENCE_APACHE2,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/prometheus/prometheus/main/README.md",
            f"{_RAW}/prometheus/prometheus/main/docs/configuration/configuration.md",
        ),
        cap=2,
    ),
)

#: CLI/data tools: the rule and configuration reference IS the whole question.
_CLI_TOOLS: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="ruff_docs",
        family="ruff",
        url="https://docs.astral.sh/ruff/",
        licence=LICENCE_MIT,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/astral-sh/ruff/main/README.md",
            f"{_RAW}/astral-sh/ruff/main/docs/configuration.md",
        ),
        cap=2,
    ),
    DocSite(
        name="valkey_docs",
        family="valkey",
        url="https://valkey.io/docs/",
        licence=LICENCE_BSD3,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(f"{_RAW}/valkey-io/valkey/unstable/README.md",),
        cap=1,
    ),
)

#: OSS-stack documentation, in fetch order.
OSS_STACK_SITES: Final[tuple[DocSite, ...]] = _DOC_TOOLS + _BI_TOOLS + _CLI_TOOLS

__all__ = ["OSS_STACK_SITES"]
