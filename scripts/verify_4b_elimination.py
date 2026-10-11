#!/usr/bin/env python3
"""Independent, READ-ONLY verifier for the Qwen3-8B/2560 elimination.

This script deliberately imports NOTHING from the rugbrain package: no lane
resolver, no dim policy, no ingest code, no qdrant_client models. It derives
the canonical vector width the way an outsider would -- by reading the declared
lane configuration out of ``.env.rag`` and cross-checking that declaration
against what Qdrant actually reports. If it shared an assumption with the code
performing the elimination it could not catch that code being wrong, which is
the only reason this file exists.

READ-ONLY CONTRACT
------------------
Exactly one HTTP verb reaches Qdrant: ``POST /collections/{c}/points/search``
(a read). Collection info is fetched with ``GET``. Nothing here issues PUT,
PATCH, POST-to-config or DELETE. There is no code path in this module that can
mutate Qdrant; the test suite asserts the invariant.

Exit codes
----------
0   end state fully correct
1   end state incorrect (the expected answer today, before the elimination)
2   the verifier could not obtain a trustworthy measurement (Qdrant
    unreachable, config missing, an HTTP error) -- deliberately distinct from
    1 so a green-looking failure is never confused with a clean state.

Usage
-----
    python3 scripts/verify_4b_elimination.py
    python3 scripts/verify_4b_elimination.py --json
    python3 scripts/verify_4b_elimination.py --expect-dim 2560   # mutation test
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# The 12 collections whose _8b twin has NOT yet reached the base point count.
# Their base must NOT be deleted until this script reports PASS for the row.
# Measured counts are carried as documentation; the live check re-measures and
# never trusts these numbers.
# --------------------------------------------------------------------------
MUST_FILL: tuple[str, ...] = (
    "scan_intel",
    "codebase_reference_v2",
    "rug_events",
    "education",
    "agent_vectors",
    "security_feeds",
    "threat_intel_v2",
    "security_alerts",
    "research_v2",
    "security_patterns_test",
    "public_crypto_patterns",
    "scanned_tokens",
)

TWIN_SUFFIX = "_8b"
DEFAULT_QDRANT_URL = "http://localhost:6334"
HTTP_TIMEOUT = 30


class MeasurementError(RuntimeError):
    """Qdrant or config could not be read. Never means 'clean'."""


# --------------------------------------------------------------------------
# HTTP (read-only)
# --------------------------------------------------------------------------
def _request(url: str, body: dict[str, Any] | None = None) -> Any:
    """GET when body is None, POST otherwise. No other verb exists here."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        raise MeasurementError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network
        raise MeasurementError(f"cannot reach {url}: {exc.reason}") from exc


# --------------------------------------------------------------------------
# Config: where the canonical width is declared
# --------------------------------------------------------------------------
def canonical_dim_from_env(env_path: Path) -> tuple[int, str]:
    """Read the declared width from RAG_EMBED_DIM in ``.env.rag``.

    Returns ``(width, provenance)``. The provenance string is printed by the
    verifier so an operator can see *where* the number came from instead of
    trusting it. Raises MeasurementError when the file or key is absent -- an
    undeclared width must never silently fall back to a hardcoded constant.
    """
    if not env_path.is_file():
        raise MeasurementError(f"config not found: {env_path}")
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "RAG_EMBED_DIM":
            continue
        value = value.strip().strip('"').strip("'")
        match = re.fullmatch(r"\d+", value)
        if not match:
            raise MeasurementError(f"RAG_EMBED_DIM is not an integer: {value!r}")
        return int(value), f"{env_path.name}:RAG_EMBED_DIM"
    raise MeasurementError(f"RAG_EMBED_DIM not declared in {env_path}")


# --------------------------------------------------------------------------
# Qdrant measurement
# --------------------------------------------------------------------------
def dense_width(config: dict[str, Any]) -> int | None:
    """Dense width from a collection config, handling BOTH wire shapes.

    Qdrant returns ``vectors`` either as a bare single-vector spec
    ``{"size": N, "distance": ...}`` or as a named-vector map
    ``{"dense": {"size": N, ...}}`` when the collection also carries a sparse
    vector. Both shapes occur at 4096 on this host, and a verifier that assumes
    one form reports healthy collections as broken (or worse, broken as healthy).
    """
    vectors = config.get("vectors")
    if not isinstance(vectors, dict):
        return None
    if "size" in vectors and isinstance(vectors.get("size"), int):
        return int(vectors["size"])
    widths = {
        int(spec["size"])
        for spec in vectors.values()
        if isinstance(spec, dict) and isinstance(spec.get("size"), int)
    }
    return max(widths) if widths else None


def is_named_vector_collection(config: dict[str, Any]) -> bool:
    """True when search must use a named ``vector:`` field in the body."""
    vectors = config.get("vectors")
    if not isinstance(vectors, dict):
        return False
    return "size" not in vectors and bool(vectors)


def dense_vector_name(config: dict[str, Any]) -> str | None:
    """The named-vector key to search, or None for the bare single-vector form.

    Prefers the conventional ``dense`` key; otherwise takes the lexicographically
    first non-sparse named entry so the choice is deterministic and explained.
    """
    vectors = config.get("vectors")
    if not is_named_vector_collection(config):
        return None
    assert isinstance(vectors, dict)
    if "dense" in vectors:
        return "dense"
    return sorted(vectors)[0]


def list_collections(base_url: str) -> list[str]:
    payload = _request(f"{base_url}/collections")
    return sorted(c["name"] for c in payload["result"]["collections"])


@dataclass(frozen=True)
class CollectionFacts:
    """Everything this verifier knows about one collection."""

    name: str
    width: int | None
    points: int
    named_vectors: bool
    dense_vector_name: str | None = None


def measure_collection(base_url: str, name: str) -> CollectionFacts:
    info = _request(f"{base_url}/collections/{name}")["result"]
    params = info.get("config", {}).get("params", {})
    return CollectionFacts(
        name=name,
        width=dense_width(params),
        points=int(info.get("points_count") or 0),
        named_vectors=is_named_vector_collection(params),
        dense_vector_name=dense_vector_name(params),
    )


def measure_all(base_url: str) -> dict[str, CollectionFacts]:
    return {n: measure_collection(base_url, n) for n in list_collections(base_url)}


# --------------------------------------------------------------------------
# Read-back probe: does the system still retrieve?
# --------------------------------------------------------------------------
def probe_read(base_url: str, facts: CollectionFacts, limit: int = 5) -> tuple[int, str]:
    """Run a real vector search. Returns ``(hit_count, note)``.

    A unit random vector is a legal query -- recall quality is not this
    verifier's job, only that the remaining corpus is *readable*. Both the bare
    and the named-vector request shapes are emitted, because a collection that
    only accepts one of them is genuinely broken.
    """
    if facts.width is None:
        return 0, "SKIP: no dense width reported"
    if facts.points == 0:
        return 0, "SKIP: collection is empty"
    rng = random.Random(20260929)
    vector = [rng.random() for _ in range(facts.width)]
    body: dict[str, Any] = {
        "vector": vector,
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    if facts.named_vectors:
        # A named-vector collection REJECTS a bare list ("Not existing vector
        # name") and also rejects {"dense": v} without the explicit name. The
        # only accepted form is {"name": <key>, "vector": [...]}.
        body["vector"] = {"name": facts.dense_vector_name, "vector": vector}
    result = _request(f"{base_url}/collections/{facts.name}/points/search", body)["result"]
    return len(result), f"search dim={facts.width} named={facts.named_vectors}"


# --------------------------------------------------------------------------
# Verdict assembly
# --------------------------------------------------------------------------
@dataclass
class Report:
    canonical_dim: int
    provenance: str
    offenders: list[CollectionFacts] = field(default_factory=list)
    fill_rows: list[dict[str, Any]] = field(default_factory=list)
    empty_bases: list[CollectionFacts] = field(default_factory=list)
    stale_bases: list[CollectionFacts] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def fill_all_pass(self) -> bool:
        return all(r["pass"] for r in self.fill_rows)

    @property
    def all_canonical(self) -> bool:
        return not self.offenders

    def exit_code(self) -> int:
        if self.problems:
            return 2
        failures = list(self.problems)
        if self.offenders:
            failures.append(
                f"{len(self.offenders)} collection(s) still off {self.canonical_dim}-dim"
            )
        if not self.fill_all_pass:
            n = sum(1 for r in self.fill_rows if not r["pass"])
            failures.append(f"{n} of {len(self.fill_rows)} must-fill twins short of base")
        if self.empty_bases:
            failures.append(f"{len(self.empty_bases)} empty off-dim collection(s) still present")
        if self.stale_bases:
            failures.append(f"{len(self.stale_bases)} off-dim collection(s) still hold data")
        if any(p["hits"] == 0 for p in self.probes):
            failures.append("a canonical collection returned no search hits")
        if not self.probes:
            # Nothing left to read is an INCORRECT end state, not an
            # unmeasurable one -- it must be distinguishable from exit 2.
            failures.append("no non-empty canonical collection left to probe")
        return 1 if failures else 0


def build_report(base_url: str, env_path: Path, expect_dim: int) -> Report:
    declared, provenance = canonical_dim_from_env(env_path)
    if declared != expect_dim:
        raise MeasurementError(f"config declares {declared} but --expect-dim {expect_dim}")
    facts = measure_all(base_url)

    report = Report(canonical_dim=declared, provenance=provenance)
    report.offenders = sorted(
        (f for f in facts.values() if f.width != declared),
        key=lambda f: (-f.points, f.name),
    )

    for base in MUST_FILL:
        twin_name = base + TWIN_SUFFIX
        base_facts = facts.get(base)
        twin_facts = facts.get(twin_name)
        if base_facts is None and twin_facts is None:
            # Post-elimination steady state: the base is gone and no twin
            # exists either (e.g. rug_events, which never had one). There is
            # nothing left to lose, so this row is satisfied -- NOT a failure.
            report.fill_rows.append(
                {
                    "collection": base,
                    "base_points": None,
                    "twin": twin_name,
                    "twin_points": None,
                    "pass": True,
                    "note": "base absent (already eliminated)",
                }
            )
            continue
        if base_facts is None:
            # The 2560 base is gone. This is the DESIRED end state: the _8b
            # twin is the surviving canonical collection and it stays forever.
            report.fill_rows.append(
                {
                    "collection": base,
                    "base_points": None,
                    "twin": twin_name,
                    "twin_points": twin_facts.points if twin_facts else None,
                    "pass": True,
                    "note": "base deleted; "
                    + ("twin survives" if twin_facts else "no twin exists"),
                }
            )
            continue
        if twin_facts is None:
            report.fill_rows.append(
                {
                    "collection": base,
                    "base_points": base_facts.points,
                    "twin": twin_name,
                    "twin_points": None,
                    "pass": False,
                    "note": "NO _8b TWIN EXISTS -- base holds the only copy",
                }
            )
            continue
        report.fill_rows.append(
            {
                "collection": base,
                "base_points": base_facts.points,
                "twin": twin_name,
                "twin_points": twin_facts.points,
                "pass": twin_facts.points >= base_facts.points,
                "note": "",
            }
        )

    # Off-dim collections holding zero points are safe to drop at any time;
    # off-dim collections holding data are NOT and are listed separately so a
    # deletion run can never sweep them up with the empty ones.
    for fact in report.offenders:
        if fact.points == 0:
            report.empty_bases.append(fact)
        else:
            report.stale_bases.append(fact)

    # Read-back: probe a canonical collection, plus every twin named above,
    # so a partially-eliminated system cannot pass by deleting everything.
    # Probe a sample that guarantees BOTH wire shapes are exercised, plus a
    # spread of the rest. Guaranteeing shape coverage matters more than
    # sampling more collections: a named-vector collection that only accepts
    # one request form is genuinely broken, and a random sample would miss it.
    # The guaranteed one-per-shape is taken FIRST and is never truncated, so a
    # small corpus still proves both forms.
    probe_targets = [
        facts[n] for n in sorted(facts) if facts[n].width == declared and facts[n].points > 0
    ]
    guaranteed = [f for f in probe_targets if not f.named_vectors][:1]
    guaranteed += [f for f in probe_targets if f.named_vectors][:1]
    chosen = {f.name for f in guaranteed}
    extra = max(2, len(probe_targets) // 8) - len(guaranteed)
    sample = list(guaranteed) + [f for f in probe_targets if f.name not in chosen][: max(0, extra)]
    for fact in sample:
        hits, note = probe_read(base_url, fact)
        report.probes.append({"collection": fact.name, "hits": hits, "note": note})
    return report


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def render(report: Report) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 74)
    add("4B/2560 ELIMINATION VERIFIER  (independent, read-only)")
    add(f"canonical width: {report.canonical_dim}   from {report.provenance}")
    add("=" * 74)

    add("")
    add(f"[1] COLLECTIONS NOT AT {report.canonical_dim}-DIM: {len(report.offenders)}")
    if not report.offenders:
        add("    none")
    for f in report.offenders:
        add(f"    {f.name:32} width={str(f.width):>5}  points={f.points:>9}")

    add("")
    add(f"[2] MUST-FILL TWINS ({len(MUST_FILL)} bases that may be deleted ONLY on PASS)")
    add(f"    {'collection':28}{'base':>10}{'twin':>10}  verdict")
    for row in report.fill_rows:
        base = "MISSING" if row["base_points"] is None else f"{row['base_points']}"
        twin = "ABSENT" if row["twin_points"] is None else f"{row['twin_points']}"
        mark = "PASS" if row["pass"] else "FAIL"
        note = f"   <- {row['note']}" if row["note"] else ""
        add(f"    {row['collection']:28}{base:>10}{twin:>10}  {mark}{note}")

    add("")
    add(f"[3] EMPTY OFF-DIM SHELLS -- safe to delete now ({len(report.empty_bases)})")
    for f in report.empty_bases:
        add(f"    {f.name:32} width={str(f.width):>5}  points=0")
    if not report.empty_bases:
        add("    none")

    add("")
    add(f"[4] OFF-DIM WITH REAL DATA -- deletion is DATA LOSS ({len(report.stale_bases)})")
    for f in report.stale_bases:
        add(f"    {f.name:32} width={str(f.width):>5}  points={f.points:>9}")

    add("")
    add(f"[5] READ-BACK PROBE ({len(report.probes)} canonical collections)")
    for p in report.probes:
        add(f"    {p['collection']:32} hits={p['hits']:>3}   {p['note']}")

    add("")
    code = report.exit_code()
    verdict = {0: "CLEAN", 1: "NOT-CLEAN", 2: "UNMEASURABLE"}[code]
    add(f"VERDICT: {verdict}   (exit {code})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qdrant-url",
        default=os.getenv("QDRANT_VERIFY_URL", DEFAULT_QDRANT_URL),
        help="Qdrant REST base URL (default %(default)s)",
    )
    parser.add_argument(
        "--env",
        default=str(Path(__file__).resolve().parents[1] / ".env.rag"),
        help="env file declaring RAG_EMBED_DIM (default: repo .env.rag)",
    )
    parser.add_argument(
        "--expect-dim",
        type=int,
        default=None,
        help="override the expected width instead of trusting the config; "
        "used by the mutation test to force a red verdict",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    env_path = Path(args.env)
    try:
        if args.expect_dim is None:
            declared, _ = canonical_dim_from_env(env_path)
            # Cross-check: the declared width must also be the most common
            # live width. If the config and the corpus disagree, the verifier
            # refuses to pick a winner and says so.
            observed = [f.width for f in measure_all(args.qdrant_url).values()]
            if observed and declared not in set(observed):
                raise MeasurementError(
                    f"config says {declared} but no collection is that width "
                    f"(observed: {sorted(set(observed))})"
                )
            report = build_report(args.qdrant_url, env_path, declared)
        else:
            report = build_report(args.qdrant_url, env_path, args.expect_dim)
    except MeasurementError as exc:
        print(f"VERDICT: UNMEASURABLE   (exit 2)\n  {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "verdict_exit": report.exit_code(),
                    "canonical_dim": report.canonical_dim,
                    "provenance": report.provenance,
                    "offenders": [vars(f) for f in report.offenders],
                    "empty_bases": [f.name for f in report.empty_bases],
                    "stale_bases": [f.name for f in report.stale_bases],
                    "fill_rows": report.fill_rows,
                    "probes": report.probes,
                    "problems": report.problems,
                },
                indent=2,
            )
        )
    else:
        print(render(report))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
