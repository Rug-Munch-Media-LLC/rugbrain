#!/usr/bin/env python3
"""reanchor_golden_set.py — re-anchor orphan rows in ``golden_set.jsonl``.

``src/rugbrain/eval/golden_set.jsonl`` carries hand-written ``gold_chunks``
labels that were never confirmed against the live corpus. After the
coding_knowledge / solana_patterns rebuild (4096-dim ``_8b`` twins), 8 rows
still point at chunks that exist NOWHERE in any ingest source — the labels
("Flash Loan Price Attack", "MEV Extraction & Sandwich Attacks", ...) are
paraphrases, not verbatim corpus text, so substring matching scores them as
permanent misses.

This tool re-anchors those rows to chunks that DO exist: for each orphan it
carries a curated (collection, gold_chunks, relevant_doc_ids) replacement
verified against the live Qdrant corpus. Rows whose anchors already resolve
are left untouched.

Usage:
    python3 scripts/reanchor_golden_set.py --dry-run   # report (default)
    python3 scripts/reanchor_golden_set.py --write     # rewrite the golden set
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
GOLDEN = Path(__file__).resolve().parent.parent / "src" / "rugbrain" / "eval" / "golden_set.jsonl"
SCROLL = 10000

#: Orphan query -> verified replacement. Each ``gold_chunks`` phrase was
#: confirmed present in the target collection's live content, and
#: ``relevant_doc_ids`` is the real Qdrant point id carrying it.
REANCHORS: dict[str, dict] = {
    "flash loan attack how it works": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "Flash loans let attackers borrow unlimited capital within one transaction",
            "weaponizing protocol logic",
        ],
        "relevant_doc_ids": ["f5362a01-26de-5587-aa7d-cbfc21c01101"],
    },
    "MEV sandwich attack prevention": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "can be submitted by anyone, they can be frontrun",
            "allow a permit call to fail",
        ],
        "relevant_doc_ids": ["1fe92d4c-c8bc-5aed-a434-1d86ba959ec4"],
    },
    "zk circuit underconstrained proof vulnerability": {
        "collection": "security_patterns",
        "gold_chunks": [
            "Underconstrained Circuit Enables Proof Forgery",
            "constraints do not fully determine the relationship between inputs and outputs",
        ],
        "relevant_doc_ids": ["3e112d3f-b690-51c1-94d1-93b50c7d92ea"],
    },
    "cross chain bridge exploits validator compromise": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "Governance exploits (Beanstalk $182M 2022, Compound 2023, many bridge multisigs) abuse privileged roles",
            "compromised admin keys",
        ],
        "relevant_doc_ids": ["979d303b-fcfb-5b10-82fa-d0358f28c554"],
    },
    "tx.origin authorization phishing vulnerability": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "SWC Registry entry SWC-115:",
            "Authorization through tx.origin",
        ],
        "relevant_doc_ids": ["3ad27c69-c8a9-587c-a9aa-10058e8db1f4"],
    },
    "secure delegatecall proxy implementation": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "check it against a whitelist of trusted contracts",
            "Delegatecall / Callcode and Libraries",
        ],
        "relevant_doc_ids": ["2ce13238-a76e-5555-a350-a3fe65997f96"],
    },
    "move language module resource signer": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "Access control in Move differs from Solidity: there is no msg.sender",
            "capability patterns give delegated authority",
        ],
        "relevant_doc_ids": ["3fb3e7e9-cef7-5f56-a128-9b4cfdc74479"],
    },
    "solana token arithmetic overflow checked": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "Arithmetic: use checked_mul/checked_add (u64 overflow in lamports/amounts)",
            "u64 overflow in lamports/amounts",
        ],
        "relevant_doc_ids": ["58e30143-ffcd-5385-9d0d-279c519fa626"],
    },
    # ── additional orphans found by scripts/golden_coverage.py ──────────
    "what was the Euler hack": {
        "collection": "defi_exploits",
        "gold_chunks": ["Title: Euler V1", "Technique: Donation Attack"],
        "relevant_doc_ids": ["5ed333ad-d5f5-58c4-ba80-7165e4243801"],
    },
    "Wormhole bridge exploit 2022 120000 ETH": {
        "collection": "defi_exploits",
        "gold_chunks": ["Rekt incident: Wormhole.", "Vulnerability class: bridge-exploit"],
        "relevant_doc_ids": ["7be20ad2-3342-5ff1-9230-8d2d93c07987"],
    },
    "oracle price manipulation flash loan lending": {
        "collection": "defi_exploits",
        "gold_chunks": ["turned a price or liquidity assumption into a system-wide loss"],
        "relevant_doc_ids": ["8087b78c-3067-535e-8b3a-f965dc843edf"],
    },
    "move capability model access control sui": {
        "collection": "coding_knowledge",
        "gold_chunks": [
            "Access control in Move differs from Solidity: there is no msg.sender",
            "capability patterns give delegated authority",
        ],
        "relevant_doc_ids": ["3fb3e7e9-cef7-5f56-a128-9b4cfdc74479"],
    },
    "honeypot token detected risk flags scanner": {
        "collection": "threat_intel_v2",
        "gold_chunks": ["Skeleton Lounge: Sorcery (SOR)"],
        "relevant_doc_ids": ["0688c1c3-8999-5df1-b1db-fe753bcbe473"],
    },
    "mev sandwich attack front run back run": {
        "collection": "threat_intel_v2",
        "gold_chunks": ["function sandWich() internal", "swap crvUSD to USDC"],
        "relevant_doc_ids": ["002db58e-b7cc-5334-94a8-6ec4bde163c4"],
    },
    "beanstalk governance attack flash loan voting": {
        "collection": "defi_exploits",
        "gold_chunks": ["Title: Beanstalk", "Technique: Flashloan Governance Attack"],
        "relevant_doc_ids": ["91603027-2548-56ae-aba5-a0a0db279a54"],
    },
}


def _scroll(collection: str, limit: int = SCROLL) -> list[dict]:
    """Fetch points from a collection (empty list when the collection is gone)."""
    req = urllib.request.Request(  # noqa: S310 — Qdrant http://
        f"{QDRANT}/collections/{collection}/points/scroll",
        data=json.dumps({"limit": limit, "with_payload": True, "with_vector": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — Qdrant http://
            return json.load(resp)["result"]["points"]
    except Exception as exc:
        print(f"  (collection {collection} unavailable: {exc})")
        return []


def _verify(collection: str, chunks: list[str], doc_ids: list[str]) -> list[str]:
    """Return the anchors that are NOT present in the live collection."""
    points = _scroll(collection)
    if not points:
        return list(chunks)
    by_id = {str(p["id"]): p for p in points}
    target = by_id.get(doc_ids[0]) if doc_ids else None
    target_content = (
        str((target.get("payload") or {}).get("content") or "").lower() if target else ""
    )
    missing = []
    for chunk in chunks:
        in_target = chunk.lower() in target_content
        in_corpus = any(
            chunk.lower() in str((p.get("payload") or {}).get("content") or "").lower()
            for p in points
        )
        if not (in_target and in_corpus):
            missing.append(chunk)
    return missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite the golden set (default: report)")
    args = ap.parse_args()

    lines = GOLDEN.read_text().splitlines()
    entries = [json.loads(line) for line in lines if line.strip() and not line.startswith("#")]

    reanchored = verified = 0
    out: list[dict] = []
    for entry in entries:
        query = str(entry.get("query") or "")
        replacement = REANCHORS.get(query)
        if replacement is None:
            out.append(entry)
            continue
        missing = _verify(
            replacement["collection"] + "_8b",
            replacement["gold_chunks"],
            replacement["relevant_doc_ids"],
        )
        if missing:
            print(f"  UNVERIFIED [{query[:50]}] missing anchors: {missing}")
            out.append(entry)
            continue
        entry["collection"] = replacement["collection"]
        entry["gold_chunks"] = replacement["gold_chunks"]
        entry["relevant_doc_ids"] = replacement["relevant_doc_ids"]
        reanchored += 1
        verified += 1
        print(f"  re-anchored [{replacement['collection']}] {query[:50]}")
        out.append(entry)

    print(f"\n{len(entries)} rows: {reanchored} re-anchored, {len(entries) - reanchored} untouched")
    if args.write:
        backup = GOLDEN.with_suffix(".jsonl.bak")
        backup.write_text(GOLDEN.read_text())
        GOLDEN.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n")
        print(f"wrote {GOLDEN} (backup: {backup.name})")
    else:
        print("(dry run — pass --write to update)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
