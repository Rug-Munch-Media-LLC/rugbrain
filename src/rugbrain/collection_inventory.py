"""Read-only inventory of every Qdrant collection, with a decision per name.

ONE QUESTION PER COLLECTION: is this name fed, read, or dead? The answer is
measured (live Qdrant point counts) plus declared (a literal reference in this
repo). A name is a CONSUMER when code/config/test names it exactly, or when it
is the derived twin (``_8b`` / ``_8b_hybrid``) of a name the lane table routes.
That second rule matters: ``lane_twin()`` builds the twin name at runtime, so a
substring grep reports 40 false orphans.

RULES (applied in order, never relaxed for a live collection):
  R1 retire   — 0 points, no consumer, no twin.
  R2 redirect — 0 points, twin has points. Plan: alias twin over the base name.
  R3 fixture  — test/scratch name, or referenced only from tests/.
  R4 rebuild  — has points at a dim other than the canonical lane (4096).
  R5 keep     — has points and a consumer.
  R6 orphan   — has points and no consumer: ALARM, never auto-deleted.

Reads GET/scroll only. Deleting is the caller's decision, never this module's.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
CANONICAL_DIM = 4096
FIXTURE_RE = re.compile(r"(^test_|_test$|^fixtures?_|slice_test|smoke)")


def _get(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        QDRANT + path,
        data=json.dumps(body).encode() if body else None,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 — host from QDRANT_URL env (operator config), path is a module literal
        return json.load(r)["result"]


def _dim(info: dict) -> int:
    cfg = info["config"]["params"]["vectors"]
    if "size" in cfg:
        return int(cfg["size"])
    return int(cfg[sorted(cfg)[0]]["size"])


def _corpus(root: str) -> dict[str, str]:
    out = subprocess.run(
        [
            "grep",
            "-rIl",
            "--exclude-dir=.git",
            "--exclude-dir=node_modules",
            "--exclude=*.bak*",
            "-e",
            ".",
            root,
        ],
        capture_output=True,
        text=True,
    ).stdout.split()
    # A report that DESCRIBES a dead collection is not a consumer of it, and
    # neither is the ratchet baseline: counting either would let the inventory
    # certify its own findings as alive.
    keep = tuple(f"{root}/{d}" for d in ("src", "scripts", "config", "tests"))
    skip = ("collection_orphans_allow.json", "collection_guard_allow.json")
    return {p: pathlib_text(p) for p in out if p.startswith(keep) and not p.endswith(skip)}


def pathlib_text(p: str) -> str:
    try:
        return open(p, errors="ignore").read()
    except OSError:
        return ""


def inventory(root: str | None = None) -> list[dict]:
    """Return one dict per collection: points, dim, twin, consumers, verdict."""
    root = root or os.getenv("RUG_BRAIN_REPO", ".")
    corpus = _corpus(root)
    names = [c["name"] for c in _get("/collections")["collections"]]
    counts, dims = {}, {}
    for n in names:
        try:
            info = _get("/collections/" + n)
        except Exception:
            continue
        counts[n], dims[n] = int(info.get("points_count") or 0), _dim(info)

    def refs(n: str) -> list[str]:
        pat = re.compile(r"\b" + re.escape(n) + r"\b")
        return [p.replace(root + "/", "") for p, t in corpus.items() if pat.search(t)]

    rows = []
    for n in sorted(names):
        twin = f"{n}_8b"
        if n.endswith("_hybrid"):
            twin = n[: -len("_hybrid")]
        elif n.endswith("_8b"):
            twin = ""
        twin_points = counts.get(twin, 0) if twin else 0
        home = refs(n)
        prod = [p for p in home if not p.startswith("tests/")]
        base = n.replace("_8b_hybrid", "").replace("_8b", "")
        derived = base != n and bool(refs(base))
        has_consumer = bool(prod) or derived
        if FIXTURE_RE.search(n):
            # A fixture referenced from production modules is the worse defect,
            # not a reason to call it used.
            verdict = "fixture"
        elif counts[n] and twin_points > 1000 and counts[n] * 100 < twin_points:
            verdict = "thin"
        elif counts[n] == 0 and twin_points > 0:
            verdict = "redirect"
        elif counts[n] == 0 and not has_consumer:
            verdict = "retire"
        elif counts[n] == 0:
            verdict = "empty-advertised"
        elif dims[n] != CANONICAL_DIM:
            verdict = "rebuild"
        elif has_consumer:
            verdict = "keep"
        else:
            verdict = "orphan"
        rows.append(
            {
                "name": n,
                "points": counts[n],
                "dim": dims[n],
                "twin": twin,
                "twin_points": twin_points,
                "verdict": verdict,
                "consumers": prod[:3],
                "test_refs": len(home) - len(prod),
            }
        )
    return rows
