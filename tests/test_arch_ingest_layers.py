"""Architecture gate: ingest sources reach I/O only through ``shared``.

The rule (ADR ``docs/ADR-ingest-architecture.md``): a source module fetches
via ``shared.http``, normalises to ``shared.normalise.Normalised`` and stores
via ``shared.store.StorePort``. It may not import an HTTP client library or
Qdrant itself.

Enforcement is a ratchet, not a clean sweep: the pre-existing violations are
frozen in ``arch_allowlist_*.txt`` and may only shrink. A new file that
imports ``httpx`` fails immediately — the deliberate-violation test below
proves the gate actually fires.
"""

from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "src" / "rugbrain" / "ingest_sources"
HERE = Path(__file__).resolve().parent
EXEMPT = ("shared",)

HTTP_IMPORT = re.compile(r"^\s*(?:import|from)\s+(httpx|aiohttp|requests)\b", re.M)
STORE_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(qdrant_client|rugbrain\.qdrant_store)\b", re.M
)


def _rel(path: Path) -> str:
    return path.relative_to(PKG).as_posix() if PKG in path.parents else path.name


def offendants(root: Path, pattern: re.Pattern[str]) -> set[str]:
    """Relative paths under *root* whose text matches *pattern*."""
    hits: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        rel = _rel(path)
        if any(part in EXEMPT for part in Path(rel).parts):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            hits.add(rel)
    return hits


def _allowlist(filename: str) -> set[str]:
    text = (HERE / filename).read_text(encoding="utf-8")
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


def test_no_new_direct_http_imports() -> None:
    new = offendants(PKG, HTTP_IMPORT) - _allowlist("arch_allowlist_httpx.txt")
    assert new == set(), f"import shared.http instead: {sorted(new)}"


def test_no_new_direct_store_imports() -> None:
    new = offendants(PKG, STORE_IMPORT) - _allowlist("arch_allowlist_qdrant.txt")
    assert new == set(), f"use shared.store.StorePort instead: {sorted(new)}"


def test_allowlists_only_shrink() -> None:
    """The frozen lists record today's debt; nothing may be added to them."""
    http = _allowlist("arch_allowlist_httpx.txt")
    store = _allowlist("arch_allowlist_qdrant.txt")
    assert len(http) <= 65, "httpx allowlist grew — the ratchet is shrink-only"
    assert len(store) <= 8, "store allowlist grew — the ratchet is shrink-only"
    assert "papers.py" not in http, "the papers slice must stay migrated"


def test_deliberate_http_violation_is_caught(tmp_path: Path) -> None:
    (tmp_path / "new_source.py").write_text("import httpx\n", encoding="utf-8")
    assert offendants(tmp_path, HTTP_IMPORT) == {"new_source.py"}


def test_deliberate_store_violation_is_caught(tmp_path: Path) -> None:
    (tmp_path / "new_source.py").write_text(
        "from rugbrain.qdrant_store import get_qdrant_store\n", encoding="utf-8"
    )
    assert offendants(tmp_path, STORE_IMPORT) == {"new_source.py"}


def test_clean_source_passes(tmp_path: Path) -> None:
    (tmp_path / "new_source.py").write_text(
        "from rugbrain.ingest_sources.shared.http import client\n", encoding="utf-8"
    )
    assert offendants(tmp_path, HTTP_IMPORT) == set()


def test_papers_family_is_registered() -> None:
    from rugbrain.ingest_sources import papers
    from rugbrain.ingest_sources.shared import registry

    entry = registry.get("papers")
    assert entry is not None
    assert entry.family == "papers"
    assert entry.fetch is papers.harvest
    assert entry.collection == papers.COLLECTION
    assert registry.by_family("papers") == (entry,)


def test_normalised_contract_is_structural() -> None:
    from rugbrain.ingest_sources.base import IngestDoc
    from rugbrain.ingest_sources.shared.normalise import Normalised, stable_doc_id

    doc = IngestDoc(content="x", doc_id="s:k:h", metadata={})
    assert isinstance(doc, Normalised)
    assert stable_doc_id("s", "k", "x") == stable_doc_id("s", "k", "x")
    assert stable_doc_id("s", "k", "x") != stable_doc_id("s", "k", "y")
