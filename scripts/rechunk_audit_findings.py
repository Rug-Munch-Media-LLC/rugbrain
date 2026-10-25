#!/usr/bin/env python3
"""Re-chunk document-level audit corpora into finding-level, mechanism-tagged chunks.

Why: `github_audits_8b` stores whole audit PDFs / repo READMEs as one 2 KB chunk, so a
question ("how does the reentrancy work?") retrieves a Next.js README instead of the
finding. This splits each stored document into individual findings and derives the
payload fields a mechanism question needs: `title`, `mechanism_tag`, `class`, `has_code`,
`source_ref`.

Dry-run by default (writes JSONL, never touches Qdrant).
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request

QDRANT = "http://127.0.0.1:6334"
MARKER = re.compile(r"(?m)^\s*(?:#{1,4}\s*)?(\[[HMLI]-\d{1,3}\]|[HMLI]-\d{1,3}\b|\d{1,2}\.\s+\*?\*?[A-Z])")
TAXONOMY = (
    ("reentrancy", r"re-?entran|reentrant"),
    ("oracle-manipulation", r"price manipul|oracle|twap|chainlink|stale price"),
    ("access-control", r"access control|onlyowner|unauthori[sz]ed|missing modifier|privilege"),
    ("precision-rounding", r"rounding|precision|truncat|decimal|scal(e|ing) factor"),
    ("signature-replay", r"signature|ecrecover|nonce|replay|eip-?712|permit"),
    ("flash-loan", r"flash ?loan|flash ?mint"),
    ("unchecked-call", r"unchecked|return value|low-?level call|\.call\{|safeapprove"),
    ("dos-griefing", r"denial of service|grief|gas limit|unbounded loop|out of gas"),
    ("initialization", r"initiali[sz]|constructor|uninitiali"),
    ("upgrade-proxy", r"proxy|upgradeable|storage collision|delegatecall|initializer"),
    ("accounting", r"accounting|share price|round ?trip|invariant|double count|fee on transfer"),
    ("input-validation", r"unvalidated|missing check|no bounds|zero address|bounds"),
    ("mev-slippage", r"slippage|front-?run|sandwich|mev|deadline"),
    ("bridge-message", r"cross-?chain|bridge|relayer|message ordering|ccip"),
)
CODE_REF = re.compile(r"(function\s+\w+|\w+\.sol\b|`[A-Za-z_]\w+(\(|::))")


def fetch(collection: str, limit: int) -> list[dict]:
    body = json.dumps({"limit": limit, "with_payload": True, "with_vector": False}).encode()
    req = urllib.request.Request(
        f"{QDRANT}/collections/{collection}/points/scroll",
        data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=25))["result"]["points"]


def mechanism(text: str) -> str:
    low = text.lower()
    for tag, pattern in TAXONOMY:
        if re.search(pattern, low):
            return tag
    return "unclassified"


def split_findings(content: str) -> list[str]:
    hits = [m.start() for m in MARKER.finditer(content)]
    if len(hits) >= 2:
        chunks = [content[a:b] for a, b in zip(hits, hits[1:] + [len(content)])]
    else:
        chunks = re.split(r"\n\s*\n", content)
    return [c.strip() for c in chunks if len(c.strip()) > 120]


def build(points: list[dict]) -> list[dict]:
    out = []
    for point in points:
        payload = point.get("payload") or {}
        content = payload.get("content") or ""
        for index, chunk in enumerate(split_findings(content)):
            head = " ".join(chunk.split())[:130]
            out.append({
                "doc_id": point["id"],
                "ordinal": index,
                "title": head,
                "mechanism_tag": mechanism(chunk),
                "class": "finding",
                "severity": payload.get("severity"),
                "has_code": bool(CODE_REF.search(chunk)),
                "source_ref": f"{payload.get('source')}::{payload.get('collection')}",
                "content": chunk,
            })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default="github_audits_8b")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--out", default="/tmp/rb_audit_findings.jsonl")
    parser.add_argument("--ask", default="")
    args = parser.parse_args()
    chunks = build(fetch(args.collection, args.limit))
    with open(args.out, "w", encoding="utf-8") as handle:
        for row in chunks:
            handle.write(json.dumps(row) + "\n")
    tagged = [c for c in chunks if c["mechanism_tag"] != "unclassified"]
    print(f"chunks={len(chunks)} tagged={len(tagged)} "
          f"with_code={sum(c['has_code'] for c in chunks)} out={args.out}")
    for row in tagged[:2]:
        print("  TAG", row["mechanism_tag"], "|", row["title"][:110])
    if args.ask:
        terms = [t for t in re.split(r"\W+", args.ask.lower()) if len(t) > 3]
        ranked = sorted(chunks, key=lambda c: -sum(t in c["content"].lower() for t in terms))
        best = ranked[0] if ranked else None
        if best:
            print("ASK:", args.ask)
            print("  mechanism_tag:", best["mechanism_tag"], "| has_code:", best["has_code"])
            print("  title:", best["title"])
            print("  excerpt:", " ".join(best["content"].split())[:400])


if __name__ == "__main__":
    main()
