"""Result types for the dimension-drift gate (split out of dim_drift.py)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugbrain.dim_drift_registry import canonical_model, retired_dims, retired_models

# ── Result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LiveCheck:
    """Verdict of the live Qdrant half."""

    canonical_dim: int
    reachable: bool
    checked: int = 0
    offenders: tuple[tuple[str, tuple[int, ...], str], ...] = ()
    unparseable: tuple[tuple[str, str], ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.reachable and not self.offenders and not self.unparseable

    def to_dict(self) -> dict[str, Any]:
        return {
            "half": "live",
            "canonical_dim": self.canonical_dim,
            "reachable": self.reachable,
            "checked": self.checked,
            "offender_count": len(self.offenders),
            "offenders": [
                {"collection": name, "dims": list(dims), "reason": reason}
                for name, dims, reason in self.offenders
            ],
            "unparseable": [
                {"collection": name, "reason": reason} for name, reason in self.unparseable
            ],
            "error": self.error,
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class StaticCheck:
    """Verdict of the static source-tree half, split by class.

    ``failures`` is the only thing that moves the exit code. ``prose`` is
    reported so a human can see it, and so a test can assert it is separated
    rather than silently merged into the pass.
    """

    scanned: int = 0
    failures: tuple[tuple[str, int, str, str], ...] = ()
    declarations: tuple[tuple[str, int, str, str], ...] = ()
    prose: tuple[tuple[str, int, str, str, bool], ...] = ()
    unparsed: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures and not self.unparsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "half": "static",
            "scanned": self.scanned,
            "ok": self.ok,
            "failure_count": len(self.failures),
            "failures": [
                {"file": f, "line": ln, "class": cls, "detail": d}
                for f, ln, cls, d in self.failures
            ],
            "registry_declaration_count": len(self.declarations),
            "registry_declarations": [
                {"file": f, "line": ln, "class": cls, "detail": d}
                for f, ln, cls, d in self.declarations
            ],
            "misleading_prose_count": len(self.prose),
            "misleading_prose": [
                {"file": f, "line": ln, "class": cls, "detail": d, "is_docstring": is_doc}
                for f, ln, cls, d, is_doc in self.prose
            ],
            "unparsed": [{"file": f, "reason": r} for f, r in self.unparsed],
        }


@dataclass(frozen=True, slots=True)
class Verification:
    """Combined verdict. ``ok`` requires BOTH halves clean."""

    live: LiveCheck
    static: StaticCheck

    @property
    def ok(self) -> bool:
        return self.live.ok and self.static.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "canonical_dim": self.live.canonical_dim,
            "retired_dims": sorted(retired_dims()),
            "retired_models": dict(retired_models()),
            "canonical_model": canonical_model(),
            "live": self.live.to_dict(),
            "static": self.static.to_dict(),
        }

    def render(self) -> str:
        """Human-readable report. Offenders are named, never counted."""
        lines: list[str] = []
        retired = ", ".join(str(d) for d in sorted(retired_dims()))
        models = ", ".join(sorted(retired_models())) or "none"
        lines.append(
            f"canonical dim {self.live.canonical_dim} "
            f"(model {canonical_model() or 'unknown'}); retired widths: {retired}; "
            f"retired models: {models}"
        )

        live = self.live
        if not live.reachable:
            lines.append(f"LIVE      UNREACHABLE  {live.error}")
        elif live.ok:
            lines.append(f"LIVE      PASS  {live.checked} collections, all at {live.canonical_dim}")
        else:
            lines.append(
                f"LIVE      FAIL  {len(live.offenders)} of {live.checked} collections "
                f"off the {live.canonical_dim} lane "
                f"({live.canonical_dim - len(live.offenders)}/{live.checked} correct)"
            )
            for name, dims, reason in live.offenders:
                shown = ",".join(str(d) for d in dims) or "none"
                lines.append(f"            {name}: dim={shown} — {reason}")
            for name, reason in live.unparseable:
                lines.append(f"            {name}: {reason}")

        st = self.static
        if st.unparsed:
            lines.append(f"STATIC    UNPARSED  {len(st.unparsed)} file(s) did not parse")
            for name, reason in st.unparsed:
                lines.append(f"            {name}: {reason}")
        if st.failures:
            lines.append(
                f"STATIC    FAIL  {len(st.failures)} executable reference(s) to the "
                f"retired generation across {st.scanned} files"
            )
            for path, line, cls, detail in st.failures:
                lines.append(f"            {path}:{line}  [{cls}] {detail}")
        else:
            lines.append(
                f"STATIC    PASS  {st.scanned} files, no executable reference to the "
                f"retired generation ({len(st.declarations)} registry declaration(s) classified)"
            )
        if st.prose:
            docstrings = sum(1 for *_, is_doc in st.prose if is_doc)
            lines.append(
                f"PROSE     REPORT  {len(st.prose)} stale claim(s) about the retired "
                f"generation ({docstrings} in docstrings) — not a failure"
            )
            for path, line, _cls, detail, is_doc in st.prose:
                kind = "docstring" if is_doc else "comment"
                lines.append(f"            {path}:{line}  [{kind}] {detail}")

        lines.append(f"RESULT    {'PASS' if self.ok else 'FAIL'}")
        return "\n".join(lines)
