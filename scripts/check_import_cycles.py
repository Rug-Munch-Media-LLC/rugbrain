#!/usr/bin/env python3
"""Import-cycle guard for ``src/rugbrain`` (module-level imports only).

Builds the import graph from TOP-LEVEL imports (function-local imports are the
sanctioned cycle-break idiom and are deliberately ignored), finds every edge
that closes a cycle, and fails when one is not in the shrink-only baseline.

    python3 scripts/check_import_cycles.py            # gate
    python3 scripts/check_import_cycles.py --write-baseline
    python3 scripts/check_import_cycles.py --strict-baseline   # also fail on prunable entries
"""

from __future__ import annotations

import ast
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "rugbrain"
BASELINE = pathlib.Path(__file__).resolve().parent / "import_cycles_baseline.txt"


def _modname(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["rugbrain", *parts]) if parts else "rugbrain"


def build_graph() -> dict[str, set[str]]:
    mods = {_modname(p): p for p in ROOT.rglob("*.py")}
    edges: dict[str, set[str]] = collections.defaultdict(set)
    for mod, path in mods.items():
        pkg = mod.rsplit(".", 1)[0] if "." in mod else ""
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = pkg.split(".")
                    base = ".".join(parts[: len(parts) - (node.level - 1)]) if node.level > 1 else pkg
                targets = [base] + [(f"{base}.{a.name}" if base else a.name) for a in node.names]
            for target in targets:
                if not target.startswith("rugbrain"):
                    continue
                cand = target
                while cand and cand not in mods and "." in cand:
                    cand = cand.rsplit(".", 1)[0]
                if cand in mods and cand != mod:
                    edges[mod].add(cand)
    return dict(edges)


def cycle_edges(edges: dict[str, set[str]]) -> list[str]:
    closed: list[str] = []
    for src, dsts in sorted(edges.items()):
        for dst in sorted(dsts):
            seen, stack, found = {dst}, [dst], False
            while stack and not found:
                cur = stack.pop()
                for nxt in edges.get(cur, ()):
                    if nxt == src:
                        found = True
                        break
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            if found:
                closed.append(f"{src} -> {dst}")
    return sorted(set(closed))


def main(argv: list[str]) -> int:
    edges = build_graph()
    found = cycle_edges(edges)
    if "--write-baseline" in argv:
        BASELINE.write_text("\n".join(found) + ("\n" if found else ""), encoding="utf-8")
        print(f"baseline written: {len(found)} cycle-closing edges -> {BASELINE}")
        return 0
    base = set()
    if BASELINE.exists():
        base = {ln.strip() for ln in BASELINE.read_text(encoding="utf-8").splitlines() if ln.strip()}
    new = [e for e in found if e not in base]
    prunable = [e for e in sorted(base) if e not in found]
    for entry in new:
        print(f"NEW CYCLE EDGE: {entry}")
    if "--strict-baseline" in argv:
        for entry in prunable:
            print(f"PRUNABLE BASELINE: {entry} (cycle already broken — delete this line)")
        if prunable:
            return 1
    print(f"import-cycles: {len(found)} cycle-closing edges, {len(base)} baselined, {len(new)} new")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
