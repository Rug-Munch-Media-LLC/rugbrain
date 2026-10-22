"""Report types for corpus verification.

Split out of verify.py on 2026-08-28 purely to clear the 200-line structure cap.
They are DATA, not behaviour: two frozen records describing what verification
found. Keeping them beside the code that produces them made the module grow, and
the cap exists precisely so modules do not sprawl.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DimMismatch:
    """A declared dim that disagrees with the live width of the same name."""

    collection: str
    declared_dim: int | None
    live_dim: int | None
    detail: str

    @property
    def is_explainable_by_retired(self) -> bool:
        """True when the live width is a dim this corpus is declared to have lived at.

        ``retired_dims`` lists the widths the OWNED NAME has itself lived at, so
        a 2560 base whose declaration writes at 4096 is the documented
        no-overwrite retention — the ``_8b`` twin holds the canonical points. The
        twin's width is deliberately NOT in that tuple: it belongs to the twin,
        not to this name, and recording it would invert the check.

        A name with NO twin therefore cannot be explained this way. A 2560
        collection declared at 4096 with nothing carrying the canonical points is
        stranded, and stranded is a finding — masking it would hide exactly the
        defect this whole package exists to surface.

        The declaration is found through :func:`gate.declared_corpus`, so this
        answers for a live ``*_8b`` name using the same declaration as its base.
        """
        from rugbrain.corpus.gate import declared_corpus

        decl = declared_corpus(self.collection)
        if decl is None or self.live_dim is None:
            return False
        return self.live_dim in decl.retired_dims


@dataclass(slots=True)
class VerificationReport:
    """Everything one verification pass found, ready to print or assert on."""

    checked: int = 0
    matched: int = 0
    mismatches: list[DimMismatch] = field(default_factory=list)
    undeclared_collections: list[str] = field(default_factory=list)
    declared_but_absent: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not (self.mismatches or self.undeclared_collections or self.declared_but_absent)

    def summary(self) -> str:
        return (
            f"checked={self.checked} matched={self.matched} "
            f"mismatches={len(self.mismatches)} "
            f"undeclared={len(self.undeclared_collections)} "
            f"declared_absent={len(self.declared_but_absent)} "
            f"unreachable={len(self.unreachable)}"
        )

