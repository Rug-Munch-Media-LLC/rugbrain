"""DeFiHackLabs source — real DeFi exploit PoCs (SunWeb3Sec/DeFiHackLabs).

Each PoC is a Foundry ``ContractTest`` in ``src/test/YYYY-MM/<Protocol>_exp.sol``
that reproduces a real exploit. ``past/*/README.md`` files carry incident
metadata (``### YYYYMMDD Name - Attack Type`` + ``### Lost: <amount>``).

The source produces two document shapes:

  * ``.sol`` PoCs  → doc_type ``exploit_poc``, content = leading comments +
    full Solidity source (chunking happens downstream).
  * ``README.md`` incident entries → doc_type ``exploit_poc``, content = the
    normalized incident line (date / protocol / attack type / amount lost).

Category + severity detection reuses the same path/keyword heuristics as
``scripts/ingest_defihacklabs.py`` so downstream filtering is consistent.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

REPO = "SunWeb3Sec/DeFiHackLabs"
REF = "main"
# Real hybrid collection in Qdrant (11.9k pts). The legacy "threat_intel"
# name was migrated to threat_intel_v2 (see scripts/migrate_to_sparse.py);
# no alias exists, so writing to the old name creates an empty fragment.
DEFAULT_COLLECTION = "threat_intel_v2"
DOC_TYPE = "exploit_poc"
DOC_TYPE_MD = "exploit_poc"

# Category → severity (matches scripts/ingest_defihacklabs.py).
SEVERITY_BY_CATEGORY: dict[str, str] = {
    "reentrancy": "critical",
    "flash-loan": "critical",
    "oracle-manipulation": "critical",
    "access-control": "high",
    "honeypot": "critical",
    "rug-pull": "critical",
    "bridge-exploit": "critical",
    "signature-malleability": "critical",
    "integer-overflow": "high",
    "precision-loss": "medium",
    "business-logic": "high",
    "governance-attack": "critical",
    "mev": "medium",
    "amm-manipulation": "high",
    "deflationary-token": "medium",
}
DEFAULT_SEVERITY = "medium"

_PATH_CATEGORIES: dict[str, str] = {
    "access_control": "access-control",
    "access-control": "access-control",
    "reentrancy": "reentrancy",
    "flashloan": "flash-loan",
    "flash-loan": "flash-loan",
    "oracle": "oracle-manipulation",
    "price_manipulation": "oracle-manipulation",
    "rugpull": "rug-pull",
    "honeypot": "honeypot",
    "mev": "mev",
    "bridge": "bridge-exploit",
    "signature": "signature-malleability",
    "overflow": "integer-overflow",
    "underflow": "integer-overflow",
    "precision": "precision-loss",
    "logic": "business-logic",
    "governance": "governance-attack",
    "uniswapv2": "amm-manipulation",
    "deflationary": "deflationary-token",
}

_CONTENT_CATEGORIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"reentrancy", re.I), "reentrancy"),
    (re.compile(r"flash ?loan", re.I), "flash-loan"),
    (re.compile(r"oracle", re.I), "oracle-manipulation"),
    (re.compile(r"price ?manipulation", re.I), "oracle-manipulation"),
    (re.compile(r"access ?control|onlyOwner", re.I), "access-control"),
    (re.compile(r"honeypot", re.I), "honeypot"),
    (re.compile(r"rug ?pull", re.I), "rug-pull"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
    (re.compile(r"signature", re.I), "signature-malleability"),
    (re.compile(r"overflow|underflow", re.I), "integer-overflow"),
    (re.compile(r"precision", re.I), "precision-loss"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"\bMEV\b", re.I), "mev"),
    (re.compile(r"deflationary", re.I), "deflationary-token"),
    (re.compile(r"logic", re.I), "business-logic"),
]

_CONTRACT_RE = re.compile(r"\bcontract\s+(\w+)")
_COMMENT_LINE_RE = re.compile(r"^\s*(//|/\*|\*)\s*(.*)$", re.MULTILINE)
_README_HEADER_RE = re.compile(r"^###\s+(\d{8})\s+(.+?)(?:\s+-\s+(.+))?$")
_README_LOST_RE = re.compile(r"^###\s+Lost:\s*(.+)$")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def detect_category(rel_path: str, code: str) -> str:
    """Category from path segments, falling back to code keywords."""
    lowered = (rel_path or "").lower()
    for key, category in _PATH_CATEGORIES.items():
        if key in lowered:
            return category
    for pattern, category in _CONTENT_CATEGORIES:
        if pattern.search(code or ""):
            return category
    return "unknown"


def parse_sol_file(rel_path: str, code: str) -> IngestDoc | None:
    """Normalize one ``.sol`` PoC into an IngestDoc."""
    if not code.strip():
        return None
    contract = _CONTRACT_RE.search(code)
    contract_name = contract.group(1) if contract else ""

    # Leading comment block = the vulnerability description.
    lines: list[str] = []
    for line in code.splitlines()[:80]:
        m = _COMMENT_LINE_RE.match(line)
        if m:
            text = m.group(2).strip()
            if text:
                lines.append(text)
        elif lines:
            break
    description = " ".join(lines)[:2000]

    category = detect_category(rel_path, code)
    severity = SEVERITY_BY_CATEGORY.get(category, DEFAULT_SEVERITY)
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    preamble = f"DeFi exploit PoC — {contract_name or rel_path}.\nCategory: {category}.\n"
    if description:
        preamble += f"Description: {description}\n"

    return IngestDoc(
        content=f"{preamble}\n```solidity\n{code}\n```",
        doc_id=f"defihacklabs:sol:{_normalize_name(rel_path)}:{_content_hash(code[:4000])}",
        doc_type=DOC_TYPE,
        source="defihacklabs",
        title=contract_name or rel_path,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "category": category,
            "severity": severity,
            "chain": "EVM",
            "ecosystem": "EVM",
            "rel_path": rel_path,
            "source_url": source_url,
            "contract_name": contract_name,
            "description": description[:500],
            "doc_type": DOC_TYPE,
        },
    )


def parse_incident_readme(rel_path: str, markdown: str) -> list[IngestDoc]:
    """Parse ``### YYYYMMDD Name - Attack Type`` incident entries from a README."""
    docs: list[IngestDoc] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        nonlocal current
        if current and "amount" in current:
            doc = _incident_doc(current, rel_path)
            if doc:
                docs.append(doc)
        current = None

    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        m = _README_HEADER_RE.match(line)
        if m:
            flush()
            current = {
                "date": m.group(1),
                "protocol": m.group(2).strip(),
                "attack_type": (m.group(3) or "").strip(),
            }
            continue
        m = _README_LOST_RE.match(line)
        if m and current:
            current["amount"] = m.group(1).strip()
            continue
        if line.startswith("### ") and current:
            # Any other ``###`` heading closes the current incident block.
            flush()
    flush()
    return docs


def _incident_doc(incident: dict[str, str], rel_path: str) -> IngestDoc | None:
    if not incident.get("protocol"):
        return None
    amount = incident.get("amount", "")
    attack = incident.get("attack_type", "")
    category = detect_category(f"{attack} {incident['protocol']}", attack)
    severity = SEVERITY_BY_CATEGORY.get(category, DEFAULT_SEVERITY)
    title = f"{incident['protocol']} — {attack or 'DeFi exploit'}"
    content = (
        f"DeFi hack incident: {incident['protocol']}.\n"
        f"Date: {incident.get('date', '') or 'unknown'}\n"
        f"Attack type: {attack or 'unknown'}\n"
        f"Amount lost: {amount or 'unknown'}\n"
        f"Category: {category}\n"
    )
    return IngestDoc(
        content=content,
        doc_id=f"defihacklabs:md:{_normalize_name(incident['protocol'])}:{incident.get('date', '')}",
        doc_type=DOC_TYPE_MD,
        source="defihacklabs",
        title=title,
        url=f"https://github.com/{REPO}/blob/{REF}/{rel_path}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": incident["protocol"],
            "date": incident.get("date", ""),
            "attack_type": attack,
            "amount_lost": amount,
            "category": category,
            "severity": severity,
            "chain": "EVM",
            "ecosystem": "EVM",
            "source_url": f"https://github.com/{REPO}/blob/{REF}/{rel_path}",
            "doc_type": DOC_TYPE_MD,
        },
    )


class DeFiHackLabsSource(Source):
    """GitHub DeFiHackLabs PoC repo (SunWeb3Sec/DeFiHackLabs)."""

    name = "defihacklabs"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Real DeFi exploit reproduction PoCs (Solidity) + incident metadata"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("defihacklabs_no_entries (rate-limited or repo moved)")
            return []

        sol_entries = pick_files(
            entries, suffixes=(".sol",), include=("src/",), exclude_dirs=("src/test/foundry", "lib")
        )
        readme_entries = pick_files(entries, suffixes=("README.md",), include=("past/",))
        sol_entries = max_size(sol_entries, limit)
        readme_entries = max_size(readme_entries, limit)

        docs: list[IngestDoc] = []
        for e in sol_entries:
            path = str(e.get("path", ""))
            code = await fetch_raw(client, REPO, REF, path)
            doc = parse_sol_file(path, code)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break

        if limit and len(docs) < limit:
            readme_entries = readme_entries[: limit - len(docs)]
        for e in readme_entries:
            path = str(e.get("path", ""))
            markdown = await fetch_raw(client, REPO, REF, path)
            docs.extend(parse_incident_readme(path, markdown))
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REPO",
    "DeFiHackLabsSource",
    "detect_category",
    "parse_incident_readme",
    "parse_sol_file",
]
