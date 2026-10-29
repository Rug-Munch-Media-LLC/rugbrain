"""Guard: every ingest source declares a storage right, and tiers hold.

Fails if a registered source has no declared tier, if a declaration is stale,
or if an excerpt_cite/pointer_only document exceeds its allowance.
"""

from __future__ import annotations

import pytest

from rugbrain.ingest.rights import (
    DECLARATIONS,
    EXCERPT_MAX_CHARS,
    RightRefused,
    StorageRight,
    UndeclaredRight,
    require_right,
)
from rugbrain.ingest.rights_enforce import RightViolation, enforce_storage
from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.registry import SOURCES


def _doc(**kw) -> IngestDoc:
    base = dict(content="x" * 10, doc_id="d1", metadata={"title": "t"},
                doc_type="post_mortem", source="s", title="t", url="https://e.x")
    base.update(kw)
    return IngestDoc(**base)


def test_every_registered_source_declares_a_right():
    missing = sorted({type(s).__name__ for s in SOURCES} - set(DECLARATIONS))
    assert not missing, f"undeclared storage right: {missing}"


def test_no_stale_declarations():
    live = {type(s).__name__ for s in SOURCES}
    stale = sorted(set(DECLARATIONS) - live)
    assert not stale, f"declared but not registered: {stale}"


def test_require_right_accepts_instances_and_names():
    for src in SOURCES:
        assert isinstance(require_right(src), StorageRight)


def test_require_right_fails_closed_on_unknown():
    with pytest.raises(UndeclaredRight):
        require_right("NoSuchSource")


def test_reject_tier_refuses_before_fetch():
    DECLARATIONS["_TestReject"] = StorageRight.REJECT
    try:
        with pytest.raises(RightRefused):
            require_right("_TestReject")
    finally:
        DECLARATIONS.pop("_TestReject")


def test_full_text_allows_whole_document():
    big = _doc(content="y" * 50_000)
    assert enforce_storage(big, StorageRight.FULL_TEXT) is big


def test_excerpt_cite_char_budget_enforced():
    assert enforce_storage(_doc(content="z" * EXCERPT_MAX_CHARS), StorageRight.EXCERPT_CITE)
    with pytest.raises(RightViolation):
        enforce_storage(_doc(content="z" * (EXCERPT_MAX_CHARS + 1)), StorageRight.EXCERPT_CITE)


def test_pointer_only_forbids_body_text():
    with pytest.raises(RightViolation):
        enforce_storage(_doc(content="body"), StorageRight.POINTER_ONLY)


def test_pointer_only_metadata_allowlist():
    ok = _doc(content="", metadata={"title": "t", "url": "u", "severity": "high"})
    assert enforce_storage(ok, StorageRight.POINTER_ONLY).content == ""
    bad = _doc(content="", metadata={"title": "t", "full_article": "copied text"})
    with pytest.raises(RightViolation):
        enforce_storage(bad, StorageRight.POINTER_ONLY)
