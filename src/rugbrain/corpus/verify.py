"""Live verification: declared dim vs live width, and undeclared collections.

Two findings, both produced by GET only.

DIM MISMATCH
    For each declaration, ask the LIVE Qdrant collection for its width
    (``lane_dims.fetch_live_dim``) and compare it to the DECLARED dim. A
    disagreement is a finding, never a silent repair: the manifest records what we
    believe, the server reports what is true, and the operator decides. All four
    historical generations still exist in the fleet (1024 retired, 2048 nemotron,
    2560 the 4B era, 4096 canonical) and a base/twin pair is the normal case, so
    a "mismatch" between a 2560 base and its 4096 twin is EXPECTED and must not
    be reported as one. The check is therefore per DECLARED NAME, against that
    name's own live width — never base-vs-twin.

UNDECLARED
    Any collection live in Qdrant that has no declaration is reported. That gap
    is the finding the operator wants: it is how a corpus gets embedded without
    ever being categorised. The reverse — declared but absent live — is also
    reported (a stale declaration), separately, because the two are different
    problems with different fixes.

No PUT, PATCH or DELETE is issued here. Read-only by construction, matching the
coverage audit's method.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from rugbrain.corpus.entries import DECLARED
from rugbrain.corpus.reports import DimMismatch, VerificationReport
from rugbrain.qdrant_store.lane_dims import fetch_live_dim

if TYPE_CHECKING:
    from rugbrain.corpus.schema import CorpusDeclaration

logger = logging.getLogger(__name__)

__all__ = [
    "DimMismatch",
    "VerificationReport",
    "verify_live_widths",
]


async def _live_collection_names(client: httpx.AsyncClient) -> set[str]:
    """Names live in Qdrant. GET ``/collections`` only."""
    response = await client.get("/collections", timeout=10.0)
    response.raise_for_status()
    result = response.json().get("result", {}) or {}
    return {c["name"] for c in result.get("collections", []) if isinstance(c, dict) and "name" in c}


def _declared_index(declared: tuple[CorpusDeclaration, ...]) -> dict[str, CorpusDeclaration]:
    return {d.collection: d for d in declared}


async def verify_live_widths(
    client: httpx.AsyncClient,
    *,
    declared: tuple[CorpusDeclaration, ...] = DECLARED,
) -> VerificationReport:
    """Probe every declared collection's live width and every live collection's
    presence in the manifest. Read-only.

    Unreachable collections (Qdrant down, probe error) are listed separately from
    mismatches so a network blip is never read as a dim defect.
    """
    report = VerificationReport()
    index = _declared_index(declared)

    try:
        live_names = await _live_collection_names(client)
    except Exception as exc:  # network layer — report, do not raise
        logger.warning("corpus_verify_collection_list_failed err=%s", exc)
        report.unreachable = sorted(index)
        return report

    # UNDECLARED: live in Qdrant, and no declaration resolves to it.
    #
    # Resolution goes through ``gate.declared_corpus``, which delegates to Lane I's
    # ``resolve_collection`` — so a live ``*_8b`` twin resolves to the same
    # declaration as its base and is NOT reported undeclared. Re-deriving the twin
    # relation here would be the second answer this package exists to remove.
    from rugbrain.corpus.gate import declared_corpus

    resolved: dict[str, CorpusDeclaration] = {}
    for name in sorted(live_names):
        decl = declared_corpus(name)
        if decl is None:
            report.undeclared_collections.append(name)
        else:
            resolved[name] = decl

    # DIM CHECK: per live NAME, against that name's own live width. A 2560 base
    # and its 4096 twin are two different names and are checked separately — the
    # pair is the expected shape, not a mismatch.
    for collection in sorted(resolved):
        decl = resolved[collection]
        report.checked += 1
        live = await fetch_live_dim(collection, client)
        if live is None:
            # Ambiguous / unparseable / probe failed — a measurement gap, not a
            # declared-vs-live disagreement. Recorded, never called a mismatch.
            report.unreachable.append(collection)
            continue
        if live == decl.dim:
            report.matched += 1
            continue
        mismatch = DimMismatch(
            collection=collection,
            declared_dim=decl.dim,
            live_dim=live,
            detail=f"declared={decl.dim} live={live} domain={decl.domain}",
        )
        if mismatch.is_explainable_by_retired:
            # Live width equals a dim this corpus is declared to have lived at:
            # documented no-overwrite retention on a retired lane (audit §1.1).
            report.matched += 1
            continue
        report.mismatches.append(mismatch)

    # A live collection whose declared dim agrees with the width the manifest
    # already RECORDED needs no further trust: the recorded observation and the
    # live probe agree, which is what makes the recorded value trustworthy.

    # DECLARED BUT ABSENT: in the manifest, and NEITHER its name nor its resolved
    # ``_8b`` twin is live. Resolved through the same resolver, so a declaration
    # whose corpus has migrated to its twin is not falsely reported absent.
    for collection in sorted(index):
        if collection in live_names:
            continue
        from rugbrain.qdrant_store.lane_resolver import resolve_collection

        twin = resolve_collection(collection)
        if not twin or twin in live_names:
            continue
        report.declared_but_absent.append(collection)
    return report
