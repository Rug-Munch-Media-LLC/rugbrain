"""DeFi incident / post-mortem / audit-finding sources.

Covers NEW incident-style sources not already ingested by ``defihacklabs``,
``rekt_news``, ``defillama_hacks``, ``peckshield`` or ``web3hacks``:

  * ``guttedefi``          — jwparktom/GutteDeFi: curated markdown table of
    DeFi attacks (date, protocol, loss, post-mortem/analysis links, audited?).
  * ``immunefi_hack_analyses`` — immunefi-team/Web3-Security-Library
    ``HackAnalyses/README.md``: root-cause post-mortems of real exploits,
    each with a vulnerability type.
  * ``blocksecteam_defi_poc`` — blocksecteam/defi_poc: per-protocol exploit
    reproduction repos (README.md + attack scripts) with tx traces.
  * ``slowmist_open_reports`` — slowmist/Knowledge-Base ``open-report``:
    published SlowMist audit reports (markdown only; PDFs are skipped).

All four normalize into ``IngestDoc`` records carrying the canonical incident
metadata (protocol, date, loss, chain, vulnerability class, source URL) so
downstream ``defi_exploits`` ingestion stays consistent with the existing
``ingest_defi_incidents.py`` pipeline.

Fail-open design (consistent with ``base.py``): any fetch or parse failure
logs a warning and returns ``[]`` — a flaky upstream never aborts a batch.
Credentials are environment-only (``GITHUB_TOKEN`` / ``PROXY_URL``).
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, pick_files

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "defi_exploits"
# SlowMist open-report entries are AUDIT FINDINGS (doc_type audit_finding),
# not incidents — per docs/DATA-PLACEMENT.md, audit findings-as-text live
# in contract_audits (registry: "OpenZeppelin + audit reports").
SLOWMIST_COLLECTION = "contract_audits"

# ── GutteDeFi ────────────────────────────────────────────────────────────
GUTTEDEFI_REPO = "jwparktom/GutteDeFi"
GUTTEDEFI_REF = "main"
GUTTEDEFI_README = "README.md"

# ── Immunefi hack analyses ───────────────────────────────────────────────
IMMUNEFI_REPO = "immunefi-team/Web3-Security-Library"
IMMUNEFI_REF = "main"
IMMUNEFI_HACK_ANALYSES_PATH = "HackAnalyses/README.md"

# ── BlockSec defi_poc ────────────────────────────────────────────────────
BLOCKSEC_REPO = "blocksecteam/defi_poc"
BLOCKSEC_REF = "main"

# ── SlowMist open reports ────────────────────────────────────────────────
SLOWMIST_REPO = "slowmist/Knowledge-Base"
SLOWMIST_REF = "master"
SLOWMIST_REPORT_DIR = "open-report"

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_FMT_RE = re.compile(r"[*_~`]+")
_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_PLAIN_URL_RE = re.compile(r"(?:https?://[^\s)\]]+)")

_LOSS_USD_RE = re.compile(r"\$?\s*([\d.,]+)\s*(mln|million|b|billion|k|m)?\b", re.IGNORECASE)

# Vulnerability keywords → class (mirrors the taxonomy used by
# scripts/ingest_defi_incidents.py so downstream filtering is consistent).
VECTOR_KEYWORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"reentrancy", re.I), "reentrancy"),
    (re.compile(r"flash ?loan", re.I), "flash-loan"),
    (re.compile(r"oracle", re.I), "oracle-manipulation"),
    (re.compile(r"price ?manipulation", re.I), "oracle-manipulation"),
    (re.compile(r"access ?control", re.I), "access-control"),
    (re.compile(r"private ?key|key compromise", re.I), "key-compromise"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"signature", re.I), "signature-malleability"),
    (re.compile(r"overflow|underflow", re.I), "integer-overflow"),
    (re.compile(r"phish", re.I), "phishing"),
    (re.compile(r"rug ?pull", re.I), "rug-pull"),
    (re.compile(r"logic", re.I), "business-logic"),
    (re.compile(r"donation", re.I), "donation-inflation"),
    (re.compile(r"approval", re.I), "approval-abuse"),
    (re.compile(r"arbitrary call", re.I), "arbitrary-call"),
    (re.compile(r"solvency|insolvency", re.I), "solvency-check"),
]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _strip_html(text: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", text or "")).strip()


def _clean_loss(text: str) -> str:
    """Strip HTML + markdown emphasis/strikethrough for display."""
    return _MD_FMT_RE.sub("", _strip_html(text)).strip()


def _links(text: str) -> list[str]:
    """Extract markdown + plain http(s) links from a cell."""
    out: list[str] = []
    for m in _LINK_RE.finditer(text or ""):
        out.append(m.group(2))
    for m in _PLAIN_URL_RE.finditer(text or ""):
        if not any(m.group(0) in u for u in out):
            out.append(m.group(0))
    return out


def _detect_class(text: str) -> str:
    for pattern, cls in VECTOR_KEYWORDS:
        if pattern.search(text or ""):
            return cls
    return "unknown"


def _parse_loss_usd(raw: str) -> float:
    """Best-effort USD loss from strings like ``$18.8 mln`` / ``~$25m``."""
    cleaned = _clean_loss(raw).replace(",", "").replace("~", "").strip()
    m = _LOSS_USD_RE.search(cleaned)
    if not m:
        return 0.0
    try:
        value = float(m.group(1))
    except ValueError:
        return 0.0
    suffix = (m.group(2) or "").lower()
    if suffix in ("mln", "million", "m"):
        value *= 1_000_000
    elif suffix in ("b", "billion"):
        value *= 1_000_000_000
    elif suffix == "k":
        value *= 1_000
    return value


def _severity_from_loss(amount: float) -> str:
    if amount >= 10_000_000:
        return "critical"
    if amount >= 1_000_000:
        return "high"
    if amount:
        return "medium"
    return "medium"


# ── GutteDeFi parser ─────────────────────────────────────────────────────

_SECTION_MONTH_RE = re.compile(r"\*\*\[(?:END OF )?(\w+\s+\d{4})\]\*\*", re.I)
_ROW_RE = re.compile(
    r"^\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|"
)


@dataclass(frozen=True)
class GutteDeFiIncident:
    """One normalized row from the GutteDeFi attack table."""

    day: str
    month_year: str
    protocol: str
    loss_raw: str
    loss_usd: float
    post_mortem_links: tuple[str, ...]
    analysis_links: tuple[str, ...]
    analysis_text: str
    audited: str
    row_index: int


def parse_guttedefi_table(markdown: str) -> list[GutteDeFiIncident]:
    """Parse the GutteDeFi README attack table into incident records.

    Table sections are delimited by ``**[Jun 2021]**`` month headers; each
    data row is ``| day | protocol | loss | post-mortem | analysis | audited |``.
    Section/TOTAL rows and empty protocol cells are skipped.
    """
    incidents: list[GutteDeFiIncident] = []
    month_year = ""
    for line in (markdown or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        m = _SECTION_MONTH_RE.search(line)
        if m:
            month_year = m.group(1)
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        day = _strip_html(m.group(1))
        protocol = _strip_html(m.group(2))
        if not protocol or protocol.lower().startswith("total"):
            continue
        loss_raw = _clean_loss(m.group(3))
        post_mortem_cell = m.group(4)
        analysis_cell = m.group(5)
        audited = _strip_html(m.group(6))
        pm_links = tuple(_links(post_mortem_cell))
        an_links = tuple(_links(analysis_cell))
        if not pm_links and not an_links:
            continue  # not an incident row (e.g. separator)
        incidents.append(
            GutteDeFiIncident(
                day=day,
                month_year=month_year,
                protocol=protocol,
                loss_raw=loss_raw,
                loss_usd=_parse_loss_usd(loss_raw),
                post_mortem_links=pm_links,
                analysis_links=an_links,
                analysis_text=_strip_html(analysis_cell),
                audited=audited,
                row_index=len(incidents),
            )
        )
    return incidents


def guttedefi_to_doc(inc: GutteDeFiIncident) -> IngestDoc:
    """Normalize one GutteDeFi incident row into an IngestDoc."""
    when = f"{inc.day} {inc.month_year}".strip()
    source_url = (
        next(iter(inc.post_mortem_links), None)
        or next(iter(inc.analysis_links), None)
        or f"https://github.com/{GUTTEDEFI_REPO}/blob/{GUTTEDEFI_REF}/{GUTTEDEFI_README}"
    )
    vuln_class = _detect_class(
        f"{inc.protocol} {inc.loss_raw} {inc.analysis_text} {' '.join(inc.post_mortem_links)}"
    )
    lines = [
        f"DeFi incident: {inc.protocol}.",
        f"Date: {when or 'unknown'}",
        f"Loss: {inc.loss_raw or 'unknown'}",
        f"Vulnerability class: {vuln_class}",
    ]
    if inc.analysis_text:
        lines.append(f"Analysis: {inc.analysis_text}")
    if inc.post_mortem_links:
        lines.append("Post-mortem: " + " ; ".join(inc.post_mortem_links))
    if inc.analysis_links:
        lines.append("Analysis: " + " ; ".join(inc.analysis_links))
    if inc.audited:
        lines.append(f"Audited: {inc.audited}")
    title = f"{inc.protocol} — DeFi incident" + (f" ({when})" if when else "")
    key = re.sub(r"[^a-z0-9]+", "-", f"{inc.protocol}-{when}".lower()).strip("-")
    return IngestDoc(
        content="\n".join(lines) + "\n",
        doc_id=f"guttedefi:{key}:{_content_hash(inc.protocol + when + inc.loss_raw)}",
        doc_type="post_mortem",
        source="guttedefi",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": inc.protocol,
            "date": when,
            "loss": inc.loss_raw,
            "loss_usd": inc.loss_usd,
            "vulnerability_class": vuln_class,
            "severity": _severity_from_loss(inc.loss_usd),
            "chain": "unknown",
            "ecosystem": "EVM",
            "source_url": source_url,
            "post_mortem_links": list(inc.post_mortem_links),
            "analysis_links": list(inc.analysis_links),
            "doc_type": "post_mortem",
        },
    )


# ── Immunefi hack analyses parser ────────────────────────────────────────

_IMMUNEFI_HEADING_RE = re.compile(
    r"^##\s+\[Hack Analysis:\s*(.+?),\s*(\w+\s+\d{4})\]\s*\((https?://[^)\s]+)\)"
)
_IMMUNEFI_VULN_RE = re.compile(r"^\s*-+\s*Vulnerability type:\s*(.+)$", re.I)


@dataclass(frozen=True)
class ImmunefiHackAnalysis:
    """One root-cause post-mortem from the Immunefi Web3-Security-Library."""

    title: str
    month_year: str
    url: str
    description: str
    vulnerability_type: str


def parse_immunefi_hack_analyses(markdown: str) -> list[ImmunefiHackAnalysis]:
    """Parse ``## [Hack Analysis: Name, Month Year](url)`` + description +
    ``- Vulnerability type: X`` entries from the HackAnalyses README."""
    entries: list[ImmunefiHackAnalysis] = []
    current: dict[str, str | list[str]] | None = None
    para: list[str] = []

    def flush() -> None:
        nonlocal current, para
        if current and current.get("title"):
            entries.append(
                ImmunefiHackAnalysis(
                    title=str(current["title"]),
                    month_year=str(current["month_year"]),
                    url=str(current["url"]),
                    description=" ".join(para).strip()[:6000],
                    vulnerability_type=str(current.get("vuln", "")),
                )
            )
        current = None
        para = []

    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _IMMUNEFI_HEADING_RE.match(line)
        if m:
            flush()
            current = {"title": m.group(1), "month_year": m.group(2), "url": m.group(3)}
            continue
        if current:
            m = _IMMUNEFI_VULN_RE.match(line)
            if m:
                current["vuln"] = m.group(1).strip()
                continue
            if line.startswith("## ") or line.startswith("# "):
                flush()
                continue
            para.append(_strip_html(line))
    flush()
    return entries


def immunefi_to_doc(a: ImmunefiHackAnalysis) -> IngestDoc:
    """Normalize one Immunefi hack analysis into an IngestDoc."""
    vuln_class = _detect_class(f"{a.title} {a.description} {a.vulnerability_type}")
    if a.vulnerability_type:
        vuln_class = _detect_class(a.vulnerability_type) or vuln_class
    content = (
        f"Immunefi hack analysis — {a.title} ({a.month_year}).\n"
        f"Vulnerability class: {vuln_class}\n"
        f"Reported vulnerability type: {a.vulnerability_type or 'unknown'}\n"
        f"Root cause:\n{a.description or '(no description)'}\n"
    )
    key = re.sub(r"[^a-z0-9]+", "-", f"{a.title}-{a.month_year}".lower()).strip("-")
    return IngestDoc(
        content=content,
        doc_id=f"immunefi_hack_analyses:{key}:{_content_hash(a.title + a.description[:2000])}",
        doc_type="post_mortem",
        source="immunefi_hack_analyses",
        title=f"Hack Analysis: {a.title}",
        url=a.url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": a.title,
            "date": a.month_year,
            "vulnerability_class": vuln_class,
            "vulnerability_type": a.vulnerability_type,
            "severity": "high",
            "chain": "unknown",
            "ecosystem": "EVM",
            "source_url": a.url,
            "doc_type": "post_mortem",
        },
    )


# ── BlockSec defi_poc normalization ──────────────────────────────────────

_PROTOCOL_FOLDER_RE = re.compile(r"^([^/]+)/README\.md$")


def blocksecteam_poc_to_doc(rel_path: str, readme: str) -> IngestDoc | None:
    """Normalize one ``<Protocol>/README.md`` into an exploit_poc IngestDoc."""
    m = _PROTOCOL_FOLDER_RE.match(rel_path)
    if not m:
        return None
    protocol = m.group(1)
    body = _strip_html(readme)[:8000]
    if not body:
        return None
    vuln_class = _detect_class(f"{protocol} {body[:4000]}")
    source_url = f"https://github.com/{BLOCKSEC_REPO}/tree/{BLOCKSEC_REF}/{protocol}"
    content = (
        f"BlockSec PoC — {protocol} exploit reproduction.\n"
        f"Vulnerability class: {vuln_class}\n"
        f"PoC folder: https://github.com/{BLOCKSEC_REPO}/tree/{BLOCKSEC_REF}/{protocol}\n\n"
        f"{body}"
    )
    return IngestDoc(
        content=content,
        doc_id=f"blocksecteam_defi_poc:{protocol.lower()}",
        doc_type="exploit_poc",
        source="blocksecteam_defi_poc",
        title=f"{protocol} — exploit PoC",
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": protocol,
            "vulnerability_class": vuln_class,
            "severity": "high",
            "chain": "unknown",
            "ecosystem": "EVM",
            "source_url": source_url,
            "rel_path": rel_path,
            "doc_type": "exploit_poc",
        },
    )


# ── SlowMist open-report normalization ───────────────────────────────────

_SLOWMIST_TITLE_RE = re.compile(r"^#\s+(.+)$")
_SLOWMIST_SUMMARY_FIELD_RE = re.compile(
    r"^\*\s*(Audit Date|Audit Result|Audit Team|Public Date):\s*(.+)$", re.I
)


def slowmist_report_to_doc(rel_path: str, markdown: str) -> IngestDoc | None:
    """Normalize one SlowMist ``open-report/*.md`` audit report."""
    body = (markdown or "").strip()
    if not body:
        return None
    title = ""
    summary: dict[str, str] = {}
    in_summary = False
    for raw in body.splitlines():
        line = raw.strip()
        m = _SLOWMIST_TITLE_RE.match(line)
        if m and not title:
            title = m.group(1).strip()
            continue
        if line.lower().startswith("## summary"):
            in_summary = True
            continue
        if line.startswith("## ") and in_summary:
            break
        if in_summary:
            m = _SLOWMIST_SUMMARY_FIELD_RE.match(line)
            if m:
                summary[m.group(1).lower()] = m.group(2).strip()
    protocol = (
        title.replace("Smart Contract Security Audit Report", "").strip() or rel_path.split("/")[-1]
    )
    source_url = f"https://github.com/{SLOWMIST_REPO}/blob/{SLOWMIST_REF}/{rel_path}"
    content = (
        f"SlowMist audit report — {title}.\n"
        + "\n".join(f"{k}: {v}" for k, v in summary.items())
        + f"\nReport: {source_url}\n\n"
        + body[:8000]
    )
    key = re.sub(r"[^a-z0-9]+", "-", protocol.lower()).strip("-")
    return IngestDoc(
        content=content,
        doc_id=f"slowmist_open_reports:{key}",
        doc_type="audit_finding",
        source="slowmist_open_reports",
        title=title or protocol,
        url=source_url,
        metadata={
            "collection": SLOWMIST_COLLECTION,
            "protocol": protocol,
            "audit_date": summary.get("audit date", ""),
            "audit_result": summary.get("audit result", ""),
            "audit_team": summary.get("audit team", ""),
            "severity": "medium",
            "chain": "unknown",
            "ecosystem": "EVM",
            "source_url": source_url,
            "rel_path": rel_path,
            "doc_type": "audit_finding",
        },
    )


# ── Source classes ───────────────────────────────────────────────────────


class GutteDeFiSource(Source):
    """jwparktom/GutteDeFi — curated markdown table of DeFi attacks."""

    name = "guttedefi"
    doc_type = "post_mortem"
    default_collection = DEFAULT_COLLECTION
    description = "GutteDeFi curated table of DeFi attacks (date, protocol, loss, post-mortems)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        markdown = await fetch_raw(client, GUTTEDEFI_REPO, GUTTEDEFI_REF, GUTTEDEFI_README)
        if not markdown:
            log.warning("guttedefi_fetch_failed (empty README)")
            return []
        incidents = parse_guttedefi_table(markdown)
        if not incidents:
            log.warning("guttedefi_parse_empty")
            return []
        if limit:
            incidents = incidents[:limit]
        return [guttedefi_to_doc(i) for i in incidents]


class ImmunefiHackAnalysesSource(Source):
    """immunefi-team/Web3-Security-Library HackAnalyses — root-cause post-mortems."""

    name = "immunefi_hack_analyses"
    doc_type = "post_mortem"
    default_collection = DEFAULT_COLLECTION
    description = "Immunefi root-cause hack analyses (real exploits, vulnerability type per case)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        markdown = await fetch_raw(client, IMMUNEFI_REPO, IMMUNEFI_REF, IMMUNEFI_HACK_ANALYSES_PATH)
        if not markdown:
            log.warning("immunefi_hack_analyses_fetch_failed")
            return []
        entries = parse_immunefi_hack_analyses(markdown)
        if not entries:
            log.warning("immunefi_hack_analyses_parse_empty")
            return []
        if limit:
            entries = entries[:limit]
        return [immunefi_to_doc(e) for e in entries]


class BlockSecDefiPocSource(Source):
    """blocksecteam/defi_poc — per-protocol exploit PoCs with README + tx traces."""

    name = "blocksecteam_defi_poc"
    doc_type = "exploit_poc"
    default_collection = DEFAULT_COLLECTION
    description = "BlockSec PoC reproductions of real DeFi exploits (README per protocol)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, BLOCKSEC_REPO, BLOCKSEC_REF)
        if not entries:
            log.warning("blocksecteam_defi_poc_tree_failed (rate-limited or repo moved)")
            return []
        readmes = pick_files(entries, suffixes=("README.md",))
        docs: list[IngestDoc] = []
        for e in readmes:
            path = str(e.get("path", ""))
            readme = await fetch_raw(client, BLOCKSEC_REPO, BLOCKSEC_REF, path)
            doc = blocksecteam_poc_to_doc(path, readme)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        return docs


class SlowMistOpenReportsSource(Source):
    """slowmist/Knowledge-Base open-report — published SlowMist audit reports.

    Audit findings route to ``contract_audits`` (not ``defi_exploits``):
    per docs/DATA-PLACEMENT.md, audit findings-as-text live in
    ``contract_audits``; only incident post-mortems belong in
    ``defi_exploits``.
    """

    name = "slowmist_open_reports"
    doc_type = "audit_finding"
    default_collection = SLOWMIST_COLLECTION
    description = "SlowMist published security audit reports (open-report, markdown only)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, SLOWMIST_REPO, SLOWMIST_REF)
        if not entries:
            log.warning("slowmist_open_reports_tree_failed (rate-limited or repo moved)")
            return []
        reports = pick_files(entries, suffixes=(".md",), include=(f"{SLOWMIST_REPORT_DIR}/",))
        docs: list[IngestDoc] = []
        for e in reports:
            path = str(e.get("path", ""))
            markdown = await fetch_raw(client, SLOWMIST_REPO, SLOWMIST_REF, path)
            doc = slowmist_report_to_doc(path, markdown)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "BLOCKSEC_REPO",
    "DEFAULT_COLLECTION",
    "GUTTEDEFI_REPO",
    "IMMUNEFI_REPO",
    "SLOWMIST_COLLECTION",
    "SLOWMIST_REPO",
    "BlockSecDefiPocSource",
    "GutteDeFiIncident",
    "GutteDeFiSource",
    "ImmunefiHackAnalysesSource",
    "ImmunefiHackAnalysis",
    "SlowMistOpenReportsSource",
    "blocksecteam_poc_to_doc",
    "guttedefi_to_doc",
    "immunefi_to_doc",
    "parse_guttedefi_table",
    "parse_immunefi_hack_analyses",
    "slowmist_report_to_doc",
]
