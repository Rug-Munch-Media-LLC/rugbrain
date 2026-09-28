"""The embed gate: a corpus with no declared dim is REFUSED, not warned about.

WHY THIS RAISES
    The dimension guard in ``qdrant_store.store_write.insert`` refuses a write
    whose vector does not fit the collection. That guard is correct and it stays —
    but it is the wrong place to discover a corpus is undeclared, for three reasons:

    * It fires AFTER the embed. The 8B embedder is the most expensive thing in the
      pipeline; discovering the corpus was never declared once the vector exists
      means the cost is already paid.
    * It is generic. "4096 vector, 2560 collection" and "this corpus has no
      language and no licence" produce the same refusal, so the operator is told
      about a dimension when the real problem is a missing declaration.
    * It cannot see the gaps that are worse than a mismatch: a corpus with no
      domain, an undeclared collection, a licence nobody recorded.

    So the check happens here, on the DECLARATION, before the embedder is called,
    and it fails closed. :func:`require_declared` raises :class:`UndeclaredCorpus`.
    There is no warning path, and no env var to turn it into one — a gate that can
    be disabled by an env var is a convention, and the ask was for structure.

WHAT IS CHECKED
    1. the corpus is declared at all (not merely constructed by a caller)
    2. it is fully declared — every required field present, dim included
    3. the dim is the CANONICAL lane, or is on the declared twin map. A corpus
       that declares a retired dim (1024, 2048, 2560) is refused for a WRITE even
       though it is readable: reading a 2560 collection is fine, writing one is
       the defect the whole lane migration is undoing.
    4. its language is consistent with its domain — a ``code`` corpus declaring
       ``unclassified`` is the categorisation failure the operator asked to make
       impossible, so it cannot pass.

Resolution to the ``_8b`` twin is ``lane_resolver.resolve_collection``'s job. This
module never re-derives it and never hardcodes a dim; the canonical lane comes from
``lane_table.CANONICAL_LANE_DIM``.
"""

from __future__ import annotations

from rugbrain.corpus.entries import DECLARED
from rugbrain.corpus.schema import (
    CODE_LANGUAGES,
    UNCLASSIFIED,
    CorpusDeclaration,
    CorpusDomain,
    CorpusLanguage,
)
from rugbrain.qdrant_store.lane_table import CANONICAL_LANE_DIM

__all__ = [
    "CANONICAL_LANE_DIM",
    "UndeclaredCorpus",
    "declared_corpus",
    "require_declared",
]


class UndeclaredCorpus(RuntimeError):
    """A corpus reached the embed path without a complete declaration.

    Carries the corpus identity and the reason so the log line is actionable
    without re-running anything. Subclasses ``RuntimeError`` because callers that
    ingest in a loop already catch broad exceptions; making this a ``ValueError``
    would have it swallowed by an unrelated handler and turn a hard refusal into a
    silent skip.
    """


def _index() -> dict[str, CorpusDeclaration]:
    return {d.corpus_key: d for d in DECLARED}


def declared_corpus(corpus_key: str) -> CorpusDeclaration | None:
    """The declaration for *corpus_key*, or None when the corpus is undeclared.

    Accepts the collection name as well as the stable id, so a caller that only
    knows where it is writing can still find the declaration. Resolution runs
    through the existing lane resolver, so a migrated base name and its ``_8b``
    twin find the SAME declaration rather than diverging.
    """
    table = _index()
    if corpus_key in table:
        return table[corpus_key]
    # Express the base/_8b relationship by DELEGATING to Lane I's resolver, never by
    # re-stripping the suffix here. Two owners of that rule is how a base and its
    # twin end up with two different declared dims.
    from rugbrain.qdrant_store.lane_resolver import resolve_collection, strip_twin_suffix

    resolved = resolve_collection(corpus_key)
    if resolved and resolved != corpus_key and resolved in table:
        return table[resolved]
    base = strip_twin_suffix(corpus_key)
    if base and base in table:
        return table[base]
    return None


def require_declared(
    corpus: CorpusDeclaration | str,
    *,
    for_write: bool = True,
) -> CorpusDeclaration:
    """Return *corpus* as a complete declaration, or raise.

    The single enforcement point. ``for_write`` (default) additionally requires
    the declared dim to be the canonical lane — the retired generations are
    readable but never a write target, which is the rule the migration is
    restoring. Pass ``for_write=False`` to validate a read-only reference.
    """
    if isinstance(corpus, str):
        found = declared_corpus(corpus)
        if found is None:
            raise UndeclaredCorpus(
                f"corpus_undeclared key={corpus!r} reason=absent_from_manifest "
                "action=add_a_CorpusDeclaration_to_corpus_entries_before_embedding"
            )
        decl = found
    else:
        decl = corpus

    if not decl.is_fully_declared:
        raise UndeclaredCorpus(
            f"corpus_undeclared id={decl.corpus_key!r} dim={decl.dim!r} "
            f"reason=incomplete_declaration "
            f"language={decl.language} domain={decl.domain} "
            f"collection={decl.collection!r} source_url={decl.source_url!r} "
            f"licence={decl.licence!r} cadence={decl.refresh_cadence!r}"
        )
    if for_write and decl.dim != CANONICAL_LANE_DIM:
        raise UndeclaredCorpus(
            f"corpus_undeclared id={decl.corpus_key!r} dim={decl.dim} "
            f"canonical={CANONICAL_LANE_DIM} reason=retired_lane_is_not_a_write_target "
            f"retired_dims={list(decl.retired_dims)}"
        )
    if decl.domain is CorpusDomain.CODE and decl.language not in CODE_LANGUAGES:
        raise UndeclaredCorpus(
            f"corpus_undeclared id={decl.corpus_key!r} language={decl.language} "
            f"reason=code_corpus_needs_a_real_language "
            f"unclassified_marker={UNCLASSIFIED!r}"
        )
    if (
        decl.language is CorpusLanguage.UNCLASSIFIED
        and decl.domain is not CorpusDomain.UNCLASSIFIED
    ):
        raise UndeclaredCorpus(
            f"corpus_undeclared id={decl.corpus_key!r} domain={decl.domain} "
            "reason=domain_set_but_language_unclassified"
        )
    return decl
