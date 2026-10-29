#!/usr/bin/env python3
"""Coverage matrix for the OSS tools we run: is there a documentation source, or a reason not.

One job: hold the register and check it. The register is the ONLY list of "every major OSS
tool the fleet operates and where its documentation comes from"; ``docs/COVERAGE-OSS-STACK.md``
is rendered from it (``--markdown``), so the matrix a reader trusts and the matrix CI checks
cannot disagree.

WHY A REGISTER AND NOT A SECOND MANIFEST
    ``devdocs_specs`` already owns what is FETCHED. This owns what is EXPECTED: the tools
    whose questions we must be able to answer, each mapped to the lane that answers it —
    a ``docs:`` library (the docs app), a devdocs family declared in
    ``devdocs_specs_oss_stack`` (this PR's group), or both. A tool with no lane is a gap.

THE THREE VERDICTS
    ``declared`` — no source existed; the family is declared in ``devdocs_specs_oss_stack``.
    ``covered``  — a source exists at a useful size; nothing to add.
    ``thin``     — a source exists but too small to answer the tool's own questions. The
                   fix is a docs-lane RESYNC, not a duplicate declaration here.
    ``blocked``  — the source is unusable; the note carries the reason, so a dead entry is
                   visible instead of quietly deleted.

    A ``declared`` verdict with no matching family is the failure this catches: the matrix
    says "we cover Grafana" while the fetcher has never heard of it.

Usage:
    python scripts/check_oss_stack_coverage.py              # check, exit 1 on a gap
    python scripts/check_oss_stack_coverage.py --markdown    # render the matrix
    python scripts/check_oss_stack_coverage.py --checks      # the retrieval probes
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Final

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rugbrain.ingest_sources.devdocs_specs_oss_stack import OSS_STACK_SITES

#: tool, lane, verdict, licence, provenance note (audit date 2026-10-25).
Register = tuple[str, str, str, str, str]

TOOLS: Final[tuple[Register, ...]] = (
    # --- declared by this PR: zero documentation points at audit time ---
    ("markitdown", "devdocs", "declared", "MIT", "README.md"),
    ("docling", "devdocs", "declared", "MIT", "README.md"),
    ("openmetadata", "devdocs", "declared", "Apache-2.0", "README + deployment/docker"),
    ("metabase", "devdocs", "declared", "AGPL-3.0", "README + config-file reference"),
    ("grafana", "devdocs", "declared", "AGPL-3.0", "README + defaults.ini + provisioning"),
    ("prometheus", "devdocs", "declared", "Apache-2.0", "README + configuration.md"),
    ("ruff", "devdocs", "declared", "MIT", "README + docs/configuration.md"),
    ("valkey", "devdocs", "declared", "BSD-3-Clause", "README (unstable branch)"),
    # --- docs lane: present. ``thin`` = a resync, never a duplicate family ---
    ("serena", "docs", "covered", "MIT", "docs:serena 154 chunks"),
    ("zoekt", "docs", "thin", "Apache-2.0", "docs:zoekt 39 chunks — resync"),
    ("metamcp", "docs", "thin", "MIT", "docs:metamcp 24 chunks — resync"),
    ("clickhouse", "docs", "covered", "Apache-2.0", "docs:clickhouse 69 chunks"),
    ("qdrant", "docs", "covered", "Apache-2.0", "docs:qdrant 104 chunks"),
    ("redis", "docs", "covered", "RSALv2/SSPL-1.0", "docs:redis 483 chunks"),
    ("neo4j", "docs", "thin", "GPL-3.0", "docs:neo4j 44 chunks — resync"),
    ("litellm", "docs", "covered", "MIT", "docs:litellm 458 chunks"),
    ("repomix", "docs", "covered", "MIT", "docs:repomix-docs 153 chunks"),
    ("docker-compose", "docs", "covered", "Apache-2.0", "docs:docker-compose 399 chunks"),
    ("openhands", "both", "covered", "MIT", "docs:docs-openhands 585 + devdocs family"),
    ("cline", "both", "covered", "Apache-2.0", "docs:docs-cline 585 + devdocs family"),
    ("codex", "both", "covered", "Apache-2.0", "docs:codex-docs 1903 + devdocs family"),
    # --- devdocs lane already declares these (rag_documents_8b) ---
    ("opencode", "devdocs", "covered", "MIT", "devdocs opencode_docs"),
    ("aider", "devdocs", "covered", "Apache-2.0", "devdocs aider_docs"),
    ("continue", "devdocs", "covered", "Apache-2.0", "devdocs continue_docs"),
    (
        "hermes",
        "devdocs",
        "thin",
        "MIT",
        "[REVIEW] devdocs family points at an upstream README, not the fleet's own Hermes — verify",
    ),
    # --- stack lane: present, some thin ---
    ("fastapi", "docs", "thin", "MIT", "docs:fastapi 69 chunks — resync"),
    ("pydantic", "docs", "covered", "MIT", "docs:pydantic 1219 chunks"),
    ("sqlalchemy", "docs", "thin", "MIT", "docs:sqlalchemy 72 chunks — resync"),
    ("pytest", "docs", "covered", "MIT", "docs:pytest 106 chunks"),
)

VERDICTS: Final[frozenset[str]] = frozenset({"declared", "covered", "thin", "blocked"})
LANES: Final[frozenset[str]] = frozenset({"devdocs", "docs", "both"})

#: The acceptance questions: what a stack question looks like, and the tool that owns it.
PROBES: Final[tuple[tuple[str, str], ...]] = (
    ("zoekt query syntax: repo:, file:, regex", "zoekt"),
    ("serena activate_project then find_symbol", "serena"),
    ("metamcp namespace gateway registration", "metamcp"),
    ("ClickHouse TTL on MergeTree", "clickhouse"),
    ("Qdrant hybrid dense sparse fusion", "qdrant"),
    ("LiteLLM router fallbacks config", "litellm"),
    ("opencode mcp.servers config json", "opencode"),
    ("Grafana provisioning dashboards yaml", "grafana"),
    ("Prometheus scrape_config relabel", "prometheus"),
    ("Ruff rule selection per-file-ignores", "ruff"),
    ("Metabase config file settings", "metabase"),
    ("MarkItDown pdf conversion", "markitdown"),
)


def problems(register: tuple[Register, ...] = TOOLS) -> list[str]:
    """Every way the register can lie about coverage. Empty list means it does not."""
    found: list[str] = []
    names = [row[0] for row in register]
    declared_families = {site.family for site in OSS_STACK_SITES}
    for name, lane, verdict, licence, note in register:
        if verdict not in VERDICTS:
            found.append(f"{name}: verdict {verdict!r} not in {sorted(VERDICTS)}")
        if lane not in LANES:
            found.append(f"{name}: lane {lane!r} not in {sorted(LANES)}")
        if not licence or not note:
            found.append(f"{name}: licence and provenance note are mandatory")
        if verdict == "blocked" and len(note) < 20:
            found.append(f"{name}: blocked needs a reason, got {note!r}")
        if verdict == "declared" and name not in declared_families:
            found.append(f"{name}: declared but no DocSite family in devdocs_specs_oss_stack")
        if verdict in {"covered", "thin"} and "docs:" not in note and "devdocs" not in note:
            found.append(f"{name}: {verdict} must name the lane that answers it")
    for duplicate in {n for n in names if names.count(n) > 1}:
        found.append(f"{duplicate}: registered twice")
    for orphan in sorted(declared_families - set(names)):
        found.append(f"{orphan}: declared family is not in the register")
    for probe, tool in PROBES:
        if tool not in names:
            found.append(f"probe {probe!r} points at unregistered tool {tool!r}")
    return found


def render_markdown(register: tuple[Register, ...] = TOOLS) -> str:
    """Project the register to the human matrix (``docs/COVERAGE-OSS-STACK.md``)."""
    lines = [
        "# OSS stack coverage — documentation lanes",
        "",
        "> Generated by `scripts/check_oss_stack_coverage.py --markdown`. Do not hand-edit;",
        "> edit the register and re-render. Audit date **2026-10-25**.",
        "",
        "| tool | lane | verdict | licence | evidence / next action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, lane, verdict, licence, note in register:
        lines.append(f"| `{name}` | {lane} | {verdict} | {licence} | {note} |")
    declared = sum(1 for row in register if row[2] == "declared")
    lines += [
        "",
        f"{len(register)} tools · {declared} newly declared · "
        f"{sum(1 for row in register if row[2] == 'thin')} thin (resync) · "
        f"{sum(1 for row in register if row[2] == 'blocked')} blocked.",
        "",
        "## Retrieval probes",
        "",
        "Each probe is a question ContextCrypto must answer about a tool we run. It is",
        "checked in two places: here (is there a source behind it?) and by the live-corpus",
        "run recorded below.",
        "",
    ]
    for probe, tool in PROBES:
        lines.append(f"- `{tool}` — {probe}")
    lines += [
        "",
        "Live run 2026-10-25 (`docs_query`, scope `*`): the seven baseline probes answered",
        "from `docs:zoekt`, `docs:serena`, `docs:metamcp`, `docs:clickhouse`, `docs:qdrant`,",
        "`internal/standards` — no empty result. The five newly-declared probes",
        "(grafana, prometheus, ruff, metabase, markitdown) return nothing until the next",
        "`ingest_all_sources --source devdocs_static` run stores the fetch.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true", help="render the matrix")
    parser.add_argument("--checks", action="store_true", help="list the retrieval probes")
    args = parser.parse_args()
    if args.markdown:
        print(render_markdown())
        return 0
    if args.checks:
        for probe, tool in PROBES:
            print(f"{tool:<14} {probe}")
        return 0
    found = problems()
    if found:
        for line in found:
            print(f"FAIL {line}")
        return 1
    print(f"OK {len(TOOLS)} tools, {len(OSS_STACK_SITES)} declared families, {len(PROBES)} probes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
