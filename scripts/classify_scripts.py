"""One-shot classifier: writes scripts/scripts_manifest.json (evidence per script)."""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ("systemd", "deploy", "Makefile", "docker-compose.yml", ".github/workflows")
HISTORICAL = re.compile(r"(migrate|backfill|seed|drop|reload|oneoff|archive|fix_|_fix|scratch|tmp_)", re.I)


def evidence_index() -> dict[str, list[str]]:
    """Map script basename -> files that reference it."""
    refs: dict[str, list[str]] = {}
    for p in ROOT.rglob("*"):
        if not p.is_file() or ".git" in p.parts or p.name.endswith((".pyc",)):
            continue
        if p.name.startswith("_parts_"):
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"([A-Za-z0-9_\-]+\.py)", text):
            refs.setdefault(m.group(1), [])
            rel = str(p.relative_to(ROOT))
            if rel not in refs[m.group(1)]:
                refs[m.group(1)].append(rel)
    return refs


def classify(name: str, refs: list[str]) -> tuple[str, list[str]]:
    """Return (kind, evidence) for one script filename."""
    runtime = [r for r in refs if r.startswith(RUNTIME)]
    if runtime:
        return "cli", runtime[:4]
    others = [r for r in refs if r not in (f"scripts/{name}",)]
    if HISTORICAL.search(name) and others:
        return "one-shot", others[:4]
    if others and not HISTORICAL.search(name):
        return "one-shot", others[:4]
    return "dead", []


def main() -> int:
    """Write the manifest; dead entries are a shrink-only ratchet baseline."""
    refs = evidence_index()
    entries = {}
    for p in sorted((ROOT / "scripts").rglob("*.py")):
        rel = str(p.relative_to(ROOT))
        if "/_parts_" in rel:
            continue
        kind, ev = classify(p.name, refs.get(p.name, []))
        entries[rel] = {"kind": kind, "evidence": ev}
    dead = sorted(k for k, v in entries.items() if v["kind"] == "dead")
    manifest = {"schema": 1, "dead_ceiling": len(dead), "scripts": entries}
    (ROOT / "scripts" / "scripts_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    counts = {k: sum(1 for v in entries.values() if v["kind"] == k) for k in ("cli", "one-shot", "dead")}
    print(counts)
    for k in dead[:20]:
        print("DEAD", k, (ROOT / k).stat().st_size if (ROOT / k).exists() else 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
