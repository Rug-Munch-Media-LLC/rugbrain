"""Public audit-finding corpora — manifest-driven, cloned at a pinned commit.

WHY THIS MODULE EXISTS
    The corpus already receives firm publications we can reach through a
    first-class source (OpenZeppelin, Spearbit, Trail of Bits). What it lacked
    is the FINDING level: the per-finding markdown that contest platforms and
    independent auditors publish, where one block is one
    (title, severity, impact, PoC, mitigation) record an auditor reasons over.
    Those live in hundreds of flat-markdown repos, so a per-firm source does
    not scale to them.

    This is the ONE fetcher for that class: a manifest of repos, each with its
    publisher, licence, doc_type and target collection. Adding another contest
    or firm repo later is a line of DATA, not new fetching code.

PINNED, NOT FLOATING
    Each doc carries the clone's ``commit_sha`` and a blob URL at that SHA, so
    a finding is citable to an exact revision and a re-ingest is reproducible.
    ``git clone --depth 1`` is used instead of the GitHub REST API because the
    unauthenticated budget is 60 req/h and a manifest sweep would spend it on
    listing alone.

LICENCE IS DATA, NOT A GUESS
    The clone does not contain the API's licence classification, so the
    manifest records what review found: an SPDX id where the repo carries one,
    ``NOASSERTION`` where GitHub could not classify it, and
    ``unlicensed-public-repo`` where no licence file exists at all. Ingesting
    unlicensed public material is a legal-review item, not a technical one —
    the doc_type/publisher on the payload is what makes that sweepable.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rugbrain.ingest_sources.base import IngestDoc, Source, log

_MAX_BYTES = 400_000
_SKIP_DIRS = frozenset({".git", "node_modules", "lib", "cache", "out", "artifacts"})


@dataclass(frozen=True)
class Repo:
    """One public repo in the manifest."""

    repo: str
    branch: str
    publisher: str
    licence: str
    doc_type: str
    collection: str
    suffixes: tuple[str, ...] = (".md",)
    paths: tuple[str, ...] = ("",)
    skip: tuple[str, ...] = ()


MANIFEST: tuple[Repo, ...] = (
    Repo("pashov/audits", "master", "Pashov Audit Group", "unlicensed-public-repo", "audit_report", "audit_reports"),
    Repo("crytic/not-so-smart-contracts", "master", "Trail of Bits (Crytic)", "Apache-2.0", "vulnerability_pattern", "security_findings"),
    Repo("SunWeb3Sec/DeFiVulnLabs", "main", "SunWeb3Sec", "unlicensed-public-repo", "exploit_poc", "security_findings", suffixes=(".md", ".sol")),
    Repo("coral-xyz/sealevel-attacks", "master", "Coral (Solana)", "unlicensed-public-repo", "vulnerability_pattern", "security_findings", suffixes=(".md", ".rs")),
    Repo("theredguild/damn-vulnerable-defi", "master", "The Red Guild", "MIT", "vulnerability_pattern", "security_findings"),
    Repo("OpenZeppelin/ethernaut", "master", "OpenZeppelin", "AGPL-3.0", "vulnerability_pattern", "security_findings", suffixes=(".sol",), paths=("contracts",)),
    Repo("sherlock-audit/2023-01-sentiment", "main", "Sherlock", "NOASSERTION", "audit_finding", "security_findings"),
    Repo("code-423n4/2023-01-astaria", "main", "Code4rena", "NOASSERTION", "audit_finding", "security_findings"),
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]


def _git(*args: str, cwd: str | None = None, timeout: int = 300) -> str:
    out = subprocess.run(  # noqa: S603 - resolved git binary, argv, no shell
        (shutil.which("git") or "git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return out.stdout.strip()


def _wanted(rel: str, spec: Repo) -> bool:
    if any(part in _SKIP_DIRS or part in spec.skip for part in Path(rel).parts[:-1]):
        return False
    if not rel.endswith(spec.suffixes):
        return False
    return any(rel.startswith(p) for p in spec.paths if p) or "" in spec.paths


def _docs_for_repo(spec: Repo, workdir: Path, cap: int) -> list[IngestDoc]:
    """Clone *spec* shallow, then one IngestDoc per matching file."""
    dest = workdir / spec.repo.replace("/", "__")
    _git("clone", "--depth", "1", "--single-branch", "--branch", spec.branch,
         f"https://github.com/{spec.repo}.git", str(dest))
    sha = _git("rev-parse", "HEAD", cwd=str(dest))
    date = _git("log", "-1", "--format=%cs", cwd=str(dest))
    files = sorted(p for p in dest.rglob("*") if p.is_file())
    docs: list[IngestDoc] = []
    for path in files:
        rel = path.relative_to(dest).as_posix()
        if not _wanted(rel, spec) or path.stat().st_size > _MAX_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if len(text) < 80:
            continue
        docs.append(
            IngestDoc(
                content=text,
                doc_id=f"audit_findings:{spec.repo}:{rel}:{sha[:8]}:{_sha(text)}",
                doc_type=spec.doc_type,
                source="public_audit_findings",
                title=f"{spec.publisher} — {rel}",
                url=f"https://github.com/{spec.repo}/blob/{sha}/{rel}",
                metadata={
                    "repo": spec.repo,
                    "path": rel,
                    "publisher": spec.publisher,
                    "licence": spec.licence,
                    "commit_sha": sha,
                    "date": date,
                    "collection": spec.collection,
                    "doc_type": spec.doc_type,
                },
            )
        )
        if len(docs) >= cap:
            break
    return docs


class PublicAuditFindingsSource(Source):
    """Finding-level corpora from the public repo manifest (see :data:`MANIFEST`)."""

    name = "public_audit_findings"
    doc_type = "audit_finding"
    default_collection = "security_findings"
    description = "Per-finding markdown/Solidity corpora from contest platforms and independent auditors"
    timeout = 900.0

    def __init__(self, repos: tuple[str, ...] = (), per_repo_cap: int = 4000) -> None:
        self.repos = repos
        self.per_repo_cap = per_repo_cap

    async def _fetch(self, client, limit: int | None = None) -> list[IngestDoc]:
        return await asyncio.to_thread(self._fetch_sync, limit)

    def _fetch_sync(self, limit: int | None) -> list[IngestDoc]:
        if not shutil.which("git"):
            log.warning("public_audit_findings_no_git")
            return []
        selected = [s for s in MANIFEST if not self.repos or s.repo in self.repos]
        docs: list[IngestDoc] = []
        with __import__("tempfile").TemporaryDirectory(prefix="audit-findings-") as tmp:
            for spec in selected:
                try:
                    got = _docs_for_repo(spec, Path(tmp), self.per_repo_cap)
                except (subprocess.SubprocessError, OSError) as exc:
                    log.warning("public_audit_findings_repo_failed repo=%s err=%s", spec.repo, str(exc)[:160])
                    continue
                log.info("public_audit_findings_repo repo=%s docs=%d", spec.repo, len(got))
                docs.extend(got)
        return docs[:limit] if limit else docs


__all__ = ["MANIFEST", "PublicAuditFindingsSource", "Repo"]
