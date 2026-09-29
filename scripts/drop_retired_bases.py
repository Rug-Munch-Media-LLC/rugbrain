#!/usr/bin/env python3
"""Delete retired-dimension Qdrant collections that are provably redundant.

    python3 scripts/drop_retired_bases.py                # dry run (DEFAULT)
    python3 scripts/drop_retired_bases.py --json         # machine-readable plan
    python3 scripts/drop_retired_bases.py --execute --yes # HUMAN DECISION

READ-ONLY BY DEFAULT. The dry run is not a courtesy flag; it is the default and
the only mode that runs unless BOTH ``--execute`` and ``--yes`` are passed.
``--execute`` without ``--yes`` refuses to do anything, so a bare ``--execute``
cannot delete by accident.

THE ASSERTION (per collection, re-checked at the moment of deletion)
    A retired base is dropped ONLY IF its ``_8b`` twin exists and
    ``twin_points >= base_points`` on a live GET, taken inside the same run that
    deletes it. The plan printed by an earlier dry run is never trusted; the
    execute path re-reads base, twin and width per collection immediately before
    each DELETE. If the assertion fails for ANY collection, that collection is
    skipped and reported, and the run continues.

Why the assertion is the whole safety story: the twins are still being written
by live ingesters while this runs, so point counts move underneath a plan. A
twin that was ahead at plan time can be behind at delete time. Hence re-read.

WHY POINT COUNTS ARE NOT PROOF OF CONTENT
    ``twin_points >= base_points`` proves the twin holds at least as many points.
    It does NOT prove it holds the same points: ids and payloads are compared by
    count, never by membership. A base that was written with content the twin
    never received would pass this assertion while being genuinely unrecoverable.
    That is why the recovery statement in docs/DROP-RETIRED-BASES.md is written
    per collection against the B2 snapshot, not inferred from point counts.

Exit codes
    0  plan computed; (in --execute) every candidate either passed or was
       skipped with a reason
    1  one or more candidates FAILED the assertion, or a collection refused on a
       width/structural guard
    2  Qdrant unreachable — UNKNOWN, not clean. Distinct from 1 on purpose:
       "cannot tell" must never be reported as "fine".
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNREACHABLE = 2

DEFAULT_QDRANT_URL = "http://127.0.0.1:6334"
CANONICAL_DIM = 4096
TWIN_SUFFIX = "_8b"
# Widths eligible for deletion. 4096 is canonical and is NEVER a candidate;
# anything not listed here (including an unparseable width) is refused.
RETIRABLE_DIMS = frozenset({1024, 2560})
_REQUEST_TIMEOUT = 20.0
_READ_ONLY = "GET"


class QdrantUnreachable(RuntimeError):
    """Raised when a GET against Qdrant cannot complete."""


def _get_json(url: str, timeout: float = _REQUEST_TIMEOUT) -> Any:
    """GET a URL and decode JSON."""
    request = urllib.request.Request(url, method=_READ_ONLY)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise QdrantUnreachable(f"GET {url} failed: {exc}") from exc


def _delete(url: str, timeout: float = _REQUEST_TIMEOUT) -> None:
    """The only method this script ever uses besides GET."""
    request = urllib.request.Request(url, method="DELETE")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            response.read()
    except urllib.error.HTTPError as exc:
        raise QdrantUnreachable(f"DELETE {url} failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QdrantUnreachable(f"DELETE {url} failed: {exc}") from exc


def parse_width(vectors: Any) -> int | None:
    """Extract the dense vector width from a Qdrant ``params.vectors`` value.

    Qdrant returns EITHER a bare single-vector object
    (``{"size": 4096, "distance": "Cosine"}``) OR a named-vector mapping
    (``{"dense": {"size": 4096, ...}, "sparse": {...}}``) when sparse vectors
    are configured. Both shapes occur on this host — including on the SAME
    collection, where ``agent_memory`` returns a bare ``vectors`` object and a
    non-empty ``sparse_vectors`` sibling.

    The two are also NOT mutually exclusive by width: an earlier attempt read
    only the named form and reported populated 4096 collections as having no
    vector config at all. Hence: try bare first, then named, and never infer
    "absent" from a shape mismatch. Returns None only when no width is present.
    """
    if not isinstance(vectors, dict):
        return None
    bare = vectors.get("size")
    if isinstance(bare, int):
        return bare
    widths = [
        spec.get("size")
        for spec in vectors.values()
        if isinstance(spec, dict) and isinstance(spec.get("size"), int)
    ]
    if widths:
        # Named vectors: the dense one is the widest dense spec. Sparse specs
        # carry no "size", so they are structurally excluded by the isinstance
        # check above rather than by ordering luck.
        return max(widths)
    return None


@dataclass(frozen=True)
class CollectionInfo:
    """One live collection, read at a specific moment."""

    name: str
    width: int | None
    points: int
    status: str

    @property
    def twin(self) -> str:
        return f"{self.name}{TWIN_SUFFIX}"


@dataclass
class Verdict:
    """Why one candidate was or was not eligible."""

    name: str
    width: int | None
    base_points: int
    twin_name: str | None
    twin_points: int | None
    eligible: bool
    reason: str
    deleted: bool = False

    @property
    def reclaimable(self) -> int:
        """Points that deletion would release — only counted when eligible."""
        return self.base_points if self.eligible else 0


@dataclass
class Plan:
    """The full, computed picture for one run."""

    verdicts: list[Verdict] = field(default_factory=list)
    refused: list[Verdict] = field(default_factory=list)

    @property
    def eligible(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.eligible]

    @property
    def reclaimable(self) -> int:
        return sum(v.reclaimable for v in self.verdicts)


def read_collection(client: Any, name: str, timeout: float = _REQUEST_TIMEOUT) -> CollectionInfo:
    """GET one collection. Raises QdrantUnreachable rather than guessing."""
    payload = _get_json(f"{client}/collections/{name}", timeout=timeout)
    if not isinstance(payload, dict) or "result" not in payload:
        raise QdrantUnreachable(f"unexpected payload shape for {name}: {type(payload).__name__}")
    result = payload["result"]
    params = (result.get("config") or {}).get("params") or {}
    return CollectionInfo(
        name=name,
        width=parse_width(params.get("vectors")),
        points=int(result.get("points_count") or 0),
        status=str(result.get("status") or "unknown"),
    )


def list_collections(client: Any, timeout: float = _REQUEST_TIMEOUT) -> list[str]:
    payload = _get_json(f"{client}/collections", timeout=timeout)
    return sorted(c["name"] for c in payload["result"]["collections"])


def classify(
    client: Any,
    *,
    empty_shells: Sequence[str] = (),
    timeout: float = _REQUEST_TIMEOUT,
) -> Plan:
    """Compute the delete plan. Pure GET; deletes nothing."""
    plan = Plan()
    names = list_collections(client, timeout=timeout)

    # --- Group B: declared empty shells. Each is re-read live: a shell that has
    # silently accumulated points since the declaration is NOT dropped.
    for name in sorted(empty_shells):
        if name not in names:
            plan.refused.append(
                Verdict(
                    name, None, 0, None, None, False, "declared empty shell but absent from Qdrant"
                )
            )
            continue
        info = read_collection(client, name, timeout=timeout)
        if info.width is None:
            plan.refused.append(
                Verdict(name, None, info.points, None, None, False, "width unparseable")
            )
        elif info.width == CANONICAL_DIM:
            plan.refused.append(
                Verdict(
                    name,
                    info.width,
                    info.points,
                    None,
                    None,
                    False,
                    f"refusing to touch canonical {CANONICAL_DIM}-dim collection",
                )
            )
        elif info.width not in RETIRABLE_DIMS:
            plan.refused.append(
                Verdict(
                    name,
                    info.width,
                    info.points,
                    None,
                    None,
                    False,
                    f"width {info.width} not in {sorted(RETIRABLE_DIMS)}",
                )
            )
        elif info.points != 0:
            plan.refused.append(
                Verdict(
                    name,
                    info.width,
                    info.points,
                    None,
                    None,
                    False,
                    f"declared empty but holds {info.points} points — not empty",
                )
            )
        else:
            plan.verdicts.append(
                Verdict(
                    name, info.width, 0, None, None, True, "empty shell: 0 points, nothing to lose"
                )
            )

    # --- Group A: retired bases whose twin is at least as large.
    # Group B is skipped here: a collection already judged as an empty shell has
    # no twin by definition, and reporting it a second time as "twin absent"
    # read as two contradictory verdicts on one collection.
    already_judged = {v.name for v in plan.verdicts} | {v.name for v in plan.refused}
    for name in names:
        if name.endswith(TWIN_SUFFIX) or name in already_judged:
            continue
        info = read_collection(client, name, timeout=timeout)
        if info.width == CANONICAL_DIM:
            continue  # canonical 4096 twin: never a candidate, not even reported
        if info.width not in RETIRABLE_DIMS:
            continue
        twin_name = info.twin
        if twin_name not in names:
            plan.refused.append(
                Verdict(
                    name, info.width, info.points, twin_name, None, False, "twin absent from Qdrant"
                )
            )
            continue
        twin = read_collection(client, twin_name, timeout=timeout)
        # The assertion, stated once. This used to be two branches: an
        # `if twin.width == CANONICAL_DIM and twin.points < base` guard plus an
        # `elif twin.points >= base` allow. For every real twin the first branch
        # already caught the failure, so the allow-branch was unreachable and
        # untested — mutating its comparison away changed no behaviour. One
        # branch, one comparison, so a mutation here is observable.
        if twin.points >= info.points:
            plan.verdicts.append(
                Verdict(
                    name,
                    info.width,
                    info.points,
                    twin_name,
                    twin.points,
                    True,
                    f"twin covers base: {twin.points} >= {info.points}",
                )
            )
        else:
            plan.verdicts.append(
                Verdict(
                    name,
                    info.width,
                    info.points,
                    twin_name,
                    twin.points,
                    False,
                    f"twin behind: {twin.points} < {info.points} (short {info.points - twin.points})",
                )
            )
    return plan


def execute(client: Any, plan: Plan, *, timeout: float = _REQUEST_TIMEOUT) -> Plan:
    """Delete eligible collections, re-asserting PER COLLECTION at delete time.

    The dry-run verdict is treated as a hypothesis, not as a fact. Base, twin
    and width are re-read immediately before each DELETE so a twin that fell
    behind between planning and deleting cannot be deleted.
    """
    for verdict in plan.eligible:
        try:
            info = read_collection(client, verdict.name, timeout=timeout)
            if info.width is None:
                raise Refusal(verdict.name, "width unparseable at delete time")
            if info.width == CANONICAL_DIM:
                raise Refusal(
                    verdict.name, f"collection became canonical {CANONICAL_DIM} — refusing"
                )
            if info.width not in RETIRABLE_DIMS:
                raise Refusal(verdict.name, f"width {info.width} no longer retirable")
            if verdict.twin_name is None:
                if info.points != 0:
                    raise Refusal(verdict.name, f"holds {info.points} points but has no twin")
                _delete(f"{client}/collections/{verdict.name}", timeout=timeout)
                verdict.deleted = True
                continue

            if verdict.twin_name not in list_collections(client, timeout=timeout):
                raise Refusal(verdict.name, f"twin {verdict.twin_name} disappeared")
            twin = read_collection(client, verdict.twin_name, timeout=timeout)
            if twin.points < info.points:
                raise Refusal(
                    verdict.name,
                    f"twin behind at delete time: {twin.points} < {info.points}",
                )
            _delete(f"{client}/collections/{verdict.name}", timeout=timeout)
            verdict.deleted = True
        except Refusal as exc:
            verdict.eligible = False
            verdict.reason = exc.reason
        except QdrantUnreachable as exc:
            verdict.eligible = False
            verdict.reason = f"SKIPPED — Qdrant unreachable mid-run: {exc}"
    return plan


class Refusal(Exception):
    """A collection that must not be deleted. Carries the operator-facing why."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name}: {reason}")
        self.name = name
        self.reason = reason


def render(plan: Plan, *, executed: bool) -> str:
    lines: list[str] = []
    header = "DELETED" if executed else "DRY RUN — nothing was deleted"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append("")
    lines.append(
        f"{'collection':28s} {'dim':>5} {'base_pts':>9} {'twin':>10} {'twin_pts':>9} {'delta':>8}  verdict"
    )
    lines.append("-" * 96)

    def sort_key(v: Verdict) -> tuple[int, int, str]:
        return (not v.eligible, -(v.twin_points or 0), v.name)

    for v in sorted(plan.verdicts, key=sort_key):
        delta = "n/a" if v.twin_points is None else f"+{v.twin_points - v.base_points}"
        if v.deleted:
            mark = "DELETED"
        elif v.eligible:
            mark = "PASS" if not executed else "PASS (re-check failed, not deleted)"
        else:
            mark = "FAIL"
        lines.append(
            f"{v.name:28s} {v.width!s:>5} {v.base_points:>9} "
            f"{(v.twin_name or '-'):>10} {v.twin_points if v.twin_points is not None else '-'!s:>9} "
            f"{delta:>8}  {mark}"
        )
        if not v.deleted:
            lines.append(f"{'':28s} -> {v.reason}")

    if plan.refused:
        lines.append("")
        lines.append("REFUSED (structural guards, never candidates):")
        for v in sorted(plan.refused, key=lambda r: r.name):
            lines.append(f"  {v.name:28s} {v.width!s:>5} pts={v.base_points:<8} -> {v.reason}")

    dropped = sum(1 for v in plan.verdicts if v.deleted)
    failed = [v for v in plan.verdicts if not v.eligible and not v.deleted]
    lines.append("")
    lines.append("TALLY")
    lines.append(f"  collections eligible : {len(plan.eligible) + len(failed)}")
    lines.append(f"  dropped              : {dropped}")
    lines.append(f"  skipped              : {len(failed)}")
    lines.append(
        f"  points reclaimed     : {sum(v.base_points for v in plan.verdicts if v.deleted)}"
    )
    if not executed:
        lines.append(f"  points reclaimable   : {plan.reclaimable}")
    if failed:
        lines.append("")
        lines.append("  SKIPPED:")
        for v in sorted(failed, key=lambda r: r.name):
            lines.append(f"    {v.name:28s} -> {v.reason}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drop retired-dim Qdrant collections whose twin covers them. Dry run by default.",
    )
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument(
        "--empty-shell",
        action="append",
        default=[],
        metavar="NAME",
        help="A 0-point collection with no twin to assert against. Repeatable.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually delete. Requires --yes.")
    parser.add_argument(
        "--yes", action="store_true", help="Explicit acknowledgement that a delete is intended."
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Machine-readable plan."
    )
    parser.add_argument("--timeout", type=float, default=_REQUEST_TIMEOUT)
    args = parser.parse_args(argv)

    if args.execute and not args.yes:
        print("REFUSING: --execute requires --yes. Nothing was deleted.", file=sys.stderr)
        return EXIT_REFUSED

    try:
        plan = classify(args.qdrant_url, empty_shells=args.empty_shell, timeout=args.timeout)
    except QdrantUnreachable as exc:
        print(
            f"Qdrant unreachable: {exc}\nUNKNOWN — nothing deleted, nothing concluded.",
            file=sys.stderr,
        )
        return EXIT_UNREACHABLE

    if args.execute:
        plan = execute(args.qdrant_url, plan, timeout=args.timeout)

    if args.as_json:
        print(
            json.dumps(
                {
                    "mode": "execute" if args.execute else "dry-run",
                    "verdicts": [
                        {
                            "name": v.name,
                            "width": v.width,
                            "base_points": v.base_points,
                            "twin": v.twin_name,
                            "twin_points": v.twin_points,
                            "eligible": v.eligible,
                            "deleted": v.deleted,
                            "reason": v.reason,
                        }
                        for v in sorted(plan.verdicts, key=lambda v: v.name)
                    ],
                    "refused": [
                        {
                            "name": v.name,
                            "width": v.width,
                            "points": v.base_points,
                            "reason": v.reason,
                        }
                        for v in sorted(plan.refused, key=lambda v: v.name)
                    ],
                    "dropped": sum(1 for v in plan.verdicts if v.deleted),
                    "reclaimable": plan.reclaimable,
                },
                indent=2,
            )
        )
    else:
        print(render(plan, executed=args.execute))

    if any(not v.eligible for v in plan.verdicts):
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
