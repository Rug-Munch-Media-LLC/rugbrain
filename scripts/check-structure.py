#!/usr/bin/env python3
"""check-structure.py — per-repo file-size ratchet gate (shrink-only).

Enforces docs/MODERNIZATION.md §2.2 / §4.3:

  * a file >= 200 lines is REPORTED (advisory),
  * a file >  500 lines is a HARD FAIL unless grandfathered in the
    shrink-only allowlist ``scripts/structure-baseline.json``,
  * an allowlisted file may never GROW (its line count may only decrease).

``--update-baseline`` regenerates the allowlist but REFUSES to raise any
existing entry or to grandfather a brand-new >500-line file. That makes the
ratchet one-way: the codebase can only get smaller per file.

Usage:
  python3 scripts/check-structure.py --repo .                 # gate
  python3 scripts/check-structure.py --repo . --update-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPORT_THRESHOLD = 200
FAIL_THRESHOLD = 500
DEFAULT_BASELINE = "scripts/structure-baseline.json"
DEFAULT_SCAN_ROOTS = ("src/rugbrain",)

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
    "mutants",
    ".tox",
    ".nox",
    "htmlcov",
}


def count_lines(path: Path) -> int:
    try:
        return len(path.read_text(errors="ignore").splitlines())
    except OSError:
        return 0


def scan(root: Path, scan_roots: tuple[str, ...]) -> dict[str, int]:
    """Map posix path -> line count for every scanned *.py >= REPORT_THRESHOLD."""
    sizes: dict[str, int] = {}
    for rel in scan_roots:
        base = root / rel
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            n = count_lines(p)
            if n >= REPORT_THRESHOLD:
                sizes[p.relative_to(root).as_posix()] = n
    return sizes


def load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("files", {})
    except (OSError, json.JSONDecodeError):
        return {}


def write_baseline(path: Path, sizes: dict[str, int]) -> None:
    payload = {
        "thresholds": {"report": REPORT_THRESHOLD, "fail": FAIL_THRESHOLD},
        "files": dict(sorted(sizes.items())),
    }
    path.write_text(json.dumps(payload, indent=1) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--scan-root", action="append", dest="scan_roots")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args()

    root = Path(args.repo).resolve()
    scan_roots = tuple(args.scan_roots or DEFAULT_SCAN_ROOTS)
    sizes = scan(root, scan_roots)
    baseline_path = root / args.baseline
    old = load_baseline(baseline_path)

    if args.update_baseline:
        bootstrap = not baseline_path.exists()
        raised = {k: (old[k], v) for k, v in sizes.items() if k in old and v > old[k]}
        new_god = [k for k, v in sizes.items() if k not in old and v > FAIL_THRESHOLD]
        if raised:
            print("REFUSING to raise the structure baseline (shrink-only):")
            for k, (o, n) in sorted(raised.items()):
                print(f"  {k}: {o} -> {n}")
            return 1
        if new_god and not bootstrap:
            print("REFUSING to grandfather NEW >500-line files (split them first):")
            for k in sorted(new_god):
                print(f"  {k}: {sizes[k]}")
            return 1
        write_baseline(baseline_path, sizes)
        print(f"structure baseline updated: {baseline_path} ({len(sizes)} files)")
        return 0

    failures: list[str] = []
    reported: list[str] = []
    for path, n in sorted(sizes.items()):
        if path in old:
            if n > old[path]:
                failures.append(f"{path}: {old[path]} -> {n} lines (allowlisted file grew)")
        elif n > FAIL_THRESHOLD:
            failures.append(f"{path}: {n} lines (NEW file >{FAIL_THRESHOLD} — split it)")
        else:
            reported.append(f"{path}: {n} lines (new >= {REPORT_THRESHOLD} — advisory)")

    shrinkable = [
        f"{path}: {base_n} -> {sizes.get(path, 0)} (run --update-baseline)"
        for path, base_n in sorted(old.items())
        if sizes.get(path, 0) < base_n
    ]

    print(
        f"structure gate: {len(sizes)} files >= {REPORT_THRESHOLD} lines (allowlisted: {len(old)})"
    )
    for line in reported:
        print(f"  [report] {line}")
    for line in shrinkable:
        print(f"  [shrink] {line}")
    if failures:
        print("\nFAIL (size ratchet):")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("structure gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
