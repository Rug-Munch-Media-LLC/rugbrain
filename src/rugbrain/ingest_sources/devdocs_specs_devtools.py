"""Declared devdocs, group: the agent and CLI dev tools this fleet actually runs.

Nine tools, each declared with the pages that answer "how do I configure or extend this",
because the failure mode with an agent CLI is never the happy path — it is a hook field
name, a permission scope, a transport flag. A generated patch that gets those wrong does
not degrade, it silently does nothing.

WHAT IS NOT HERE. Marketing pages, pricing, changelogs and tutorial series: one page that
states the contract beats ten that restate it. Raw ``README.md`` is preferred wherever the
project publishes one (it arrives clean); the docs sites are HTML and are stripped, which
is lossy but bounded.

LICENCES ARE READ, NOT ASSUMED. Each value below was taken from the repository's own
LICENSE file on the probe run (2026-10-25), not from a marketplace listing. Claude Code is
the exception and is declared as vendor documentation terms — the corpus is internal, so
it is usable, but a product-bound pass must be able to see that the page is not
redistributable.

PROBE, DO NOT TRUST THE DECLARATION. ``scripts/refresh_devdocs.py`` reports failures per
source; a dead path stays visible there instead of being quietly removed from a list that
then looks healthy.

See ``devdocs_schema`` for the field contract and ``devdocs_specs`` for the aggregate.
"""

from __future__ import annotations

from typing import Final

from rugbrain.ingest_sources.devdocs_schema import (
    DOCS_COLLECTION,
    LICENCE_APACHE2,
    LICENCE_MIT,
    LICENCE_PROPRIETARY_DOCS,
    DocSite,
)

_RAW = "https://raw.githubusercontent.com"

_AGENT_TOOLS: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="opencode_docs",
        family="opencode",
        url="https://opencode.ai/docs/",
        licence=LICENCE_MIT,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/sst/opencode/dev/README.md",
            "https://opencode.ai/docs/cli/",
            "https://opencode.ai/docs/agents/",
        ),
        cap=3,
    ),
    DocSite(
        name="aider_docs",
        family="aider",
        url="https://aider.chat/docs/",
        licence=LICENCE_APACHE2,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/Aider-AI/aider/main/README.md",
            "https://aider.chat/docs/usage.html",
        ),
        cap=2,
    ),
    DocSite(
        name="hermes_agent",
        family="hermes",
        url="https://github.com/NousResearch/Hermes-Agent",
        licence=LICENCE_MIT,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(f"{_RAW}/NousResearch/Hermes-Agent/main/README.md",),
        cap=1,
    ),
    DocSite(
        name="openhands_docs",
        family="openhands",
        url="https://docs.all-hands.dev/",
        licence=LICENCE_MIT,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/All-Hands-AI/OpenHands/main/README.md",
            "https://docs.all-hands.dev/usage/how-to/custom-sandbox-guide",
        ),
        cap=2,
    ),
    DocSite(
        name="cline_docs",
        family="cline",
        url="https://github.com/cline/cline",
        licence=LICENCE_APACHE2,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(f"{_RAW}/cline/cline/main/README.md",),
        cap=1,
    ),
    DocSite(
        name="continue_docs",
        family="continue",
        url="https://github.com/continuedev/continue",
        licence=LICENCE_APACHE2,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(f"{_RAW}/continuedev/continue/main/README.md",),
        cap=1,
    ),
    DocSite(
        name="codex_docs",
        family="codex",
        url="https://github.com/openai/codex",
        licence=LICENCE_APACHE2,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/openai/codex/main/README.md",
            f"{_RAW}/openai/codex/main/docs/config.md",
        ),
        cap=2,
    ),
    DocSite(
        name="claude_code_docs",
        family="claude-code",
        url="https://docs.claude.com/en/docs/claude-code/overview",
        licence=LICENCE_PROPRIETARY_DOCS,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://docs.claude.com/en/docs/claude-code/overview",
            "https://docs.claude.com/en/docs/claude-code/hooks",
            "https://docs.claude.com/en/docs/claude-code/mcp",
        ),
        cap=3,
    ),
    DocSite(
        name="kilocode_docs",
        family="kilocode",
        url="https://github.com/Kilo-Org/kilocode",
        licence=LICENCE_MIT,
        doc_type="tool_docs",
        collection=DOCS_COLLECTION,
        urls=(f"{_RAW}/Kilo-Org/kilocode/main/README.md",),
        cap=1,
    ),
)

#: Agent/CLI tool docs, in fetch order.
DEVTOOL_SITES: Final[tuple[DocSite, ...]] = _AGENT_TOOLS

__all__ = ["DEVTOOL_SITES"]
