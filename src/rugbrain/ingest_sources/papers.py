"""Papers/standards ingest — EIPs (CC0) + DeFiLlama protocol metadata.

Closes the gap where ``research_v2`` held only 158 RSS headline+lede rows
(so "find the peer-reviewed AMM paper" was grounded-in-absence) while the
``standards`` collection sat at **0 points**.

This module is the thin orchestrator: network + counters + the operator
dry-run. All normalization lives in :mod:`papers_norm` (pure, tested).

Safety contract:

  * **DRY RUN BY DEFAULT.** Nothing is written unless a human runs it
    for real; ``--execute`` deliberately refuses and defers to the
    reviewed ingest pipeline.
  * **Deterministic point ids** (``papers_norm.point_id``) so a re-run
    upserts rather than duplicates.
  * **Additive only** — no delete, no re-dim, no touch of ``research_v2``.

Licence posture (operator ruling 2026-09-29): this corpus is INTERNAL and
never shipped, so CC-BY-NC and registration-gated sources ARE usable; the
operative test is redistribution, not use. Every licence is recorded in
``data/papers_license.md`` so the day this feeds a shipped product we know
which rows were never cleared for it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field

import httpx

from rugbrain.ingest_sources.papers_norm import (
    COLLECTION,
    DEFILLAMA_URL,
    EIP_PATH_RE,
    EIPS_REF,
    EIPS_REPO,
    EMBEDDING_LANE,
    VECTOR_SIZE,
    SourceDoc,
    parse_eip,
    protocol_doc,
)

log = logging.getLogger(__name__)


@dataclass
class Harvest:
    """Docs plus the counters an operator needs to trust the number."""

    docs: list[SourceDoc] = field(default_factory=list)
    seen: int = 0
    stubs: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def substantive(self) -> int:
        return len(self.docs)


async def harvest_eips(client: httpx.AsyncClient, limit: int | None) -> Harvest:
    """Fetch the EIPs tree, then each substantive EIP markdown file."""
    h = Harvest()
    tree_url = f"https://api.github.com/repos/{EIPS_REPO}/git/trees/{EIPS_REF}?recursive=1"
    r = await client.get(tree_url, headers={"Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        h.errors.append(f"eips tree HTTP {r.status_code}")
        return h
    paths = [e["path"] for e in r.json().get("tree", []) if EIP_PATH_RE.match(e.get("path", ""))]
    h.seen = len(paths)
    if limit:
        paths = paths[:limit]
    for path in paths:
        raw = f"https://raw.githubusercontent.com/{EIPS_REPO}/{EIPS_REF}/{path}"
        resp = await client.get(raw)
        if resp.status_code != 200:
            h.errors.append(f"{path} HTTP {resp.status_code}")
            continue
        doc = parse_eip(path, resp.text, raw)
        if doc is None:
            h.stubs += 1
        else:
            h.docs.append(doc)
    return h


async def harvest_defillama(client: httpx.AsyncClient, limit: int | None) -> Harvest:
    """Fetch the DeFiLlama /protocols bulk array."""
    h = Harvest()
    r = await client.get(DEFILLAMA_URL)
    if r.status_code != 200:
        h.errors.append(f"defillama HTTP {r.status_code}")
        return h
    rows = r.json()
    h.seen = len(rows)
    if limit:
        rows = rows[:limit]
    for row in rows:
        doc = protocol_doc(row)
        if doc is not None:
            h.docs.append(doc)
    return h


async def harvest(client: httpx.AsyncClient, limit: int | None) -> Harvest:
    """Both sources, one combined ``Harvest``."""
    combined = Harvest()
    for coro in (harvest_eips(client, limit), harvest_defillama(client, limit)):
        part = await coro
        combined.docs.extend(part.docs)
        combined.seen += part.seen
        combined.stubs += part.stubs
        combined.errors.extend(part.errors[:5])
    return combined


def report(h: Harvest) -> str:
    """The operator-facing dry-run summary."""
    by_source: dict[str, int] = {}
    for d in h.docs:
        by_source[d.source] = by_source.get(d.source, 0) + 1
    lines = [
        f"DRY RUN — nothing written. collection={COLLECTION} lane={EMBEDDING_LANE}:{VECTOR_SIZE}",
        f"  candidates seen upstream : {h.seen}",
        f"  would ingest             : {h.substantive}",
        f"  skipped (moved stubs)    : {h.stubs}",
    ]
    lines += [f"    - {src:<10} {n}" for src, n in sorted(by_source.items())]
    lines.append(f"  fetch errors             : {len(h.errors)}")
    lines += [f"    ! {e}" for e in h.errors[:5]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Dry-run by default; ``--execute`` is the only path that writes."""
    p = argparse.ArgumentParser(description="Harvest papers/standards (EIPs + DeFiLlama)")
    p.add_argument("--execute", action="store_true", help="actually write (default: dry run)")
    p.add_argument("--limit", type=int, default=None, help="max docs per source")
    p.add_argument("--out", default=None, help="write docs as JSONL here")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def run() -> Harvest:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await harvest(client, args.limit)

    h = asyncio.run(run())
    print(report(h))
    if args.out:
        with open(args.out, "w") as fh:
            for d in h.docs:
                fh.write(json.dumps(d.__dict__, default=str) + "\n")
        print(f"  sample written           : {args.out}")
    if not args.execute:
        print("  (dry run — pass --execute to write)")
        return 0
    # Real writes are intentionally NOT implemented here: an operator must
    # run this through the reviewed ingest pipeline so the embedder, the
    # 4096-lane check and the licence stamp are all applied by one owner.
    print("  REFUSING: writes must go through the reviewed ingest pipeline", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
