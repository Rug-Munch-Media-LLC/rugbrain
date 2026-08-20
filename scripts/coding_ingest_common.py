#!/usr/bin/env python3
"""Shared helpers for the RugBrain coding-knowledge ingesters.

Used by ``ingest_solidity_knowledge.py``, ``ingest_rust_solana_knowledge.py``,
``ingest_alt_vm_knowledge.py`` and ``ingest_security_guides.py`` so every
script embeds with the same Infinity batch path (2560-dim qwen3-embedding-4b), writes the
canonical Qdrant payload shape (via ``ingest_common2``) and tags every point
with coding metadata (ecosystem, language, knowledge_type, topic).

Provides:
    fetch_text          — HTTP text fetch with browser UA + one retry
    html_to_text        — stdlib HTML → readable text (no bs4 dependency)
    markdown_sections   — split markdown into heading-scoped chunks
    rst_to_text         — rough reStructuredText → plain text
    build_coding_doc    — canonical coding-knowledge doc dict
    ingest_coding_docs  — write_docs wrapper for the coding_knowledge collection
    sparse_clone        — cheap git clone for large repos (blob:none + sparse)
    collect_markdown    — gather .md files under include dirs (deterministic)

Scripts import this module directly:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from coding_ingest_common import build_coding_doc, ingest_coding_docs

No secrets: all defaults point at local services (Qdrant / Infinity) and are
overridable via the standard RAG env vars (QDRANT_URL, INFINITY_URL,
RAG_INFINITY_EMBED_MODEL).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore

logger = logging.getLogger(__name__)

from ingest_common2 import build_doc, write_docs  # noqa: E402

# Collection used by all coding-knowledge ingesters (Part 5 routes code-intent
# search queries here first).
CODING_COLLECTION = "coding_knowledge"

# Default browser-ish UA so docs sites don't 403 us.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 RugBrain-Ingest/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,text/markdown,text/plain,*/*;q=0.8",
}

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "blockquote",
    "section",
    "article",
    "tr",
    "table",
    "dt",
    "dd",
    "hr",
}
_SKIP_TAGS = {"script", "style", "noscript", "head", "meta", "link", "svg"}


class _TextExtractor(HTMLParser):
    """Collect visible text from HTML, one line per block element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.lines: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buf.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        if text:
            self.lines.append(text)
        self._buf = []

    def text(self) -> str:
        self._flush()
        return "\n".join(self.lines)


def html_to_text(raw: str) -> str:
    """Strip tags/scripts from *raw* HTML and return readable text.

    Uses only stdlib ``html.parser`` so ingesters have no bs4 dependency.
    """
    if not raw:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("html_parse_fail err=%s", str(e)[:120])
        return ""
    return parser.text()


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    request_timeout: float = 30.0,
    max_bytes: int = 4_000_000,
) -> str:
    """GET *url* and return its body as text ("" on failure, retries once)."""
    for attempt in (1, 2):
        try:
            r = await client.get(
                url, headers=DEFAULT_HEADERS, timeout=request_timeout, follow_redirects=True
            )
            if r.status_code in (429, 500, 502, 503, 504) and attempt == 1:
                await asyncio_sleep(2.0)
                continue
            if r.status_code != 200:
                logger.warning("fetch_fail url=%s status=%d", url, r.status_code)
                return ""
            body = r.content[:max_bytes]
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError:
                return body.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning("fetch_error url=%s attempt=%d err=%s", url, attempt, str(e)[:120])
            if attempt == 1:
                await asyncio_sleep(1.0)
    return ""


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


# ── Text splitting ────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_RST_HEADING_RE = re.compile(r"^([A-Za-z0-9][^\n]{0,120})\n([=\-~^\"']{3,})\s*$", re.MULTILINE)


def markdown_sections(
    text: str, *, max_len: int = 2000, min_len: int = 120
) -> list[dict[str, str]]:
    """Split markdown *text* into heading-scoped chunks.

    Returns ``[{"heading": str, "text": str}]``. Sections are merged when
    smaller than ``min_len`` and split when larger than ``max_len`` (by
    paragraph boundaries, then fixed windows).
    """
    text = (text or "").strip()
    if not text:
        return []
    # Split into (heading, body) groups.
    groups: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            groups.append((current_heading, current_body))
            current_heading = m.group(2).strip()
            current_body = []
        else:
            current_body.append(line)
    groups.append((current_heading, current_body))

    out: list[dict[str, str]] = []
    for heading, body_lines in groups:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        if len(body) <= max_len:
            if len(body) >= min_len:
                out.append({"heading": heading or "", "text": body})
            continue
        # Long section: split on paragraph boundaries first.
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        acc: list[str] = []
        acc_len = 0
        for para in paragraphs:
            if acc and acc_len + len(para) > max_len:
                out.append({"heading": heading or "", "text": "\n\n".join(acc)})
                acc, acc_len = [], 0
            acc.append(para)
            acc_len += len(para)
        if acc:
            out.append({"heading": heading or "", "text": "\n\n".join(acc)})
    return out


def rst_to_text(rst: str) -> str:
    """Coarse reStructuredText → plain text (strips directives/roles).

    Good enough for ingestion: keeps paragraphs and indented code blocks,
    drops ``.. _label:`` references and ``:role:`` markup.
    """
    text = (rst or "").strip()
    if not text:
        return ""
    # Drop explicit targets / directives (.. foo::).
    lines: list[str] = []
    in_directive = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(".. ") and (":" in stripped.split(" ")[0] or len(stripped) < 60):
            in_directive = True
            continue
        if in_directive:
            if stripped == "" or stripped.startswith(".. "):
                in_directive = False
                continue
            if line.startswith("   ") or line.startswith("\t"):
                continue
            in_directive = False
        if not stripped:
            lines.append("")
            continue
        # Role markup: :code:`x` → x, ``x`` → x, *x* → x, **x** → x.
        line = re.sub(r":\w+:`([^`]*)`", r"\1", line)
        line = re.sub(r"``([^`]*)``", r"\1", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"\*([^*]+)\*", r"\1", line)
        lines.append(line)
    return "\n".join(lines)


# ── Doc builders ──────────────────────────────────────────────────────


def build_coding_doc(
    *,
    source: str,
    rel_key: str,
    content: str,
    ecosystem: str,
    language: str,
    knowledge_type: str,
    topic: str = "",
    title: str = "",
    url: str = "",
    severity: str = "info",
    chain: str = "",
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a canonical coding-knowledge doc for the Qdrant payload shape.

    ``rel_key`` is a stable source-specific identifier (e.g. a doc page path)
    — hashed with the content so re-runs overwrite the same points.
    """
    metadata: dict[str, Any] = {
        "ecosystem": ecosystem,
        "language": language,
        "knowledge_type": knowledge_type,
        "topic": topic,
        "title": title,
        "source_url": url,
        "coding_knowledge": True,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return build_doc(
        source=f"coding:{source}",
        rel_key=rel_key,
        content=content,
        metadata=metadata,
        severity=severity,
        chain=chain or ecosystem,
        collection=CODING_COLLECTION,
    )


async def ingest_coding_docs(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    docs: Sequence[dict[str, Any]],
    *,
    source: str,
    dry_run: bool = False,
    chunk_size: int = 2000,
    chunk_overlap: int = 100,
) -> dict[str, int]:
    """Chunk/embed/upsert *docs* into ``coding_knowledge`` (see write_docs)."""
    return await write_docs(
        client,
        store,
        docs,
        source=f"coding:{source}",
        collection=CODING_COLLECTION,
        dry_run=dry_run,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


# ── Repo handling ─────────────────────────────────────────────────────


def sparse_clone(repo: str, ref: str, include_paths: Sequence[str], dest: Path) -> Path:
    """Clone *repo* cheaply: depth 1, blob:none filter, sparse checkout.

    Only the blobs for *include_paths* download, which keeps multi-GB repos
    (solana, cairo, move) tractable. Idempotent — returns existing dir.
    """
    if (dest / ".git").exists():
        logger.info("repo_present repo=%s dir=%s", repo, dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ck-"))
    try:
        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--single-branch",
        ]
        if ref:
            cmd += ["--branch", ref]
        cmd += [repo, str(tmp)]
        result = subprocess.run(  # noqa: S603 — repo/ref are fixed constants
            cmd, capture_output=True, text=True, timeout=900
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed for {repo}: {result.stderr[-300:]}")
        checkout_cmd = ["git", "-C", str(tmp), "sparse-checkout", "set", *include_paths]
        checkout = subprocess.run(  # noqa: S603
            checkout_cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if checkout.returncode != 0:
            logger.warning("sparse_checkout_partial repo=%s err=%s", repo, checkout.stderr[-200:])
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(tmp), str(dest))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    size_mb = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file()) / 1e6
    logger.info("clone_complete repo=%s size_mb=%.1f", repo, size_mb)
    return dest


SKIP_DIRS = {"node_modules", "target", ".git", "venv", ".venv", "book"}
SKIP_MD_PARTS = (".t.sol", ".test", "mock", "changelog", "license")


def collect_markdown(
    repo_root: Path,
    include_paths: Sequence[str],
    limit: int = 400,
) -> list[Path]:
    """Return .md files under *include_paths*, skipping junk (deterministic)."""
    files: list[Path] = []
    for inc in include_paths:
        base = repo_root / inc
        if not base.exists():
            logger.warning("include_missing dir=%s", inc)
            continue
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(repo_root).as_posix()
            parts = rel.replace("\\", "/").split("/")
            if any(part in SKIP_DIRS for part in parts):
                continue
            lowered = rel.lower()
            if any(part in lowered for part in SKIP_MD_PARTS):
                continue
            files.append(p)
            if len(files) >= limit:
                return files
    return files[:limit]


def read_text(path: Path, max_chars: int = 60_000) -> str:
    """Read a repo file as UTF-8 text, truncated defensively."""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except OSError as e:
        logger.warning("read_failed path=%s err=%s", path, str(e)[:120])
        return ""


def normalize_source_id(name: str) -> str:
    """Slugify a source name for rel_key/metadata use."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def first_lines(text: str, n: int = 3) -> str:
    """Return the first *n* non-empty lines of *text* (for titles)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " — ".join(lines[:n])[:200]


__all__ = [
    "CODING_COLLECTION",
    "build_coding_doc",
    "collect_markdown",
    "fetch_text",
    "first_lines",
    "html_to_text",
    "ingest_coding_docs",
    "markdown_sections",
    "normalize_source_id",
    "read_text",
    "rst_to_text",
    "sparse_clone",
]
