"""Static half of the dimension-drift gate: walk the tree, aggregate findings."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from rugbrain.dim_drift_file_scan import _scan_file
from rugbrain.dim_drift_files import _iter_python_files
from rugbrain.dim_drift_registry import retired_dims, retired_models
from rugbrain.dim_drift_types import StaticCheck


def scan_static(
    roots: Sequence[Path | str] | None = None,
    *,
    skip: Sequence[Path | str] = (),
) -> StaticCheck:
    """AST-walk the tree for executable references to the retired generation.

    Parsing rather than grepping is the load-bearing choice: a plain text scan
    cannot tell a comment from a default, and this tree has 100+ files that
    mention 2560 in prose while only a handful carry a live default. Docstrings
    and comments are peeled off and reported on their own track.
    """
    base = Path(__file__).resolve().parents[2]
    if roots is None:
        roots = [base / "src", base / "scripts"]
    root_paths = [Path(r) for r in roots]
    skip_paths = {Path(s).resolve() for s in skip}

    retired = retired_dims()
    models = retired_models()

    failures: list[tuple[str, int, str, str]] = []
    declarations: list[tuple[str, int, str, str]] = []
    prose: list[tuple[str, int, str, str, bool]] = []
    unparsed: list[tuple[str, str]] = []
    scanned = 0

    for path in _iter_python_files(root_paths):
        scanned_file, _rel, f_prose, f_decl, f_fail, f_unparsed = _scan_file(
            path, base, retired, models, skip_paths
        )
        if scanned_file:
            scanned += 1
        prose.extend(f_prose)
        declarations.extend(f_decl)
        failures.extend(f_fail)
        unparsed.extend(f_unparsed)

    return StaticCheck(
        scanned=scanned,
        failures=tuple(failures),
        declarations=tuple(declarations),
        prose=tuple(prose),
        unparsed=tuple(unparsed),
    )
