"""Guard: every script under scripts/ must be declared in scripts_manifest.json.

A new file with no declaration fails. Dead entries may only shrink
(``dead_ceiling`` is a ratchet).
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "scripts_manifest.json"
VALID = {"cli", "one-shot", "dead"}


def scripts_on_disk() -> set[str]:
    """All first-class script modules (split implementation modules are excluded)."""
    return {
        str(p.relative_to(ROOT))
        for p in (ROOT / "scripts").rglob("*.py")
        if "/_parts_" not in str(p.relative_to(ROOT))
    }


def check() -> list[str]:
    """Return violations; empty list means the guard passes."""
    if not MANIFEST.exists():
        return [f"missing manifest: {MANIFEST}"]
    data = json.loads(MANIFEST.read_text())
    declared = data.get("scripts", {})
    problems = []
    for rel in sorted(scripts_on_disk() - set(declared)):
        problems.append(f"undeclared script: {rel} (declare it as cli|one-shot in scripts_manifest.json)")
    for rel, meta in sorted(declared.items()):
        if not (ROOT / rel).exists():
            problems.append(f"stale manifest entry: {rel}")
        elif meta.get("kind") not in VALID:
            problems.append(f"invalid kind for {rel}: {meta.get('kind')!r}")
        elif meta.get("kind") == "cli" and not meta.get("evidence"):
            problems.append(f"cli script without evidence: {rel}")
        elif meta.get("kind") == "one-shot" and not meta.get("evidence"):
            problems.append(f"one-shot script without declaring evidence: {rel}")
    dead = sum(1 for m in declared.values() if m.get("kind") == "dead")
    if dead > data.get("dead_ceiling", 0):
        problems.append(f"dead count grew: {dead} > ceiling {data['dead_ceiling']} (ratchet is shrink-only)")
    return problems


def main() -> int:
    """Print violations and exit non-zero when the guard fails."""
    problems = check()
    for p in problems:
        print(p)
    print(f"check_scripts_manifest: {len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
