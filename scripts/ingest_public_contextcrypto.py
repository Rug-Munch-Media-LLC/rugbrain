#!/usr/bin/env python3
"""Ingest a reviewed ContextCrypto public projection into RugBrain.

The input must be JSONL containing only records that passed the public
projection contract (audience=public, an approved public source kind, license
and external source URL present). Public/open-source code snippets are allowed
only when explicitly marked ``source_kind=contextcrypto_public_code`` and
``public_safe=true``. Raw ContextCrypto records are not read from Qdrant here.
The default is a dry run; ``--apply`` writes the redacted documents to the
canonical public collection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from rugbrain.embeddings import get_embedding
from rugbrain.public_projection import (
    PUBLIC_PROJECTION_COLLECTION,
    project_contextcrypto_record,
    projection_metadata,
)
from rugbrain.qdrant_store import QdrantVectorStore


def _records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number}: record must be an object")
        records.append(value)
    return records


async def _ingest(records: list[dict[str, Any]], apply: bool) -> dict[str, int]:
    projected = [project_contextcrypto_record(record) for record in records]
    if not apply:
        return {"validated": len(projected), "written": 0}

    written = 0
    store = QdrantVectorStore()
    try:
        for record in projected:
            embedding = await get_embedding(record["content"])
            if not embedding:
                continue
            ok = await store.insert(
                doc_id=f"contextcrypto-public:{record['content_hash']}",
                collection=PUBLIC_PROJECTION_COLLECTION,
                embedding=embedding,
                content=record["content"],
                metadata=projection_metadata(record),
                source=record.get("provenance", {}).get("source_url", ""),
                severity=str(record.get("severity", "medium")),
            )
            written += int(ok)
    finally:
        await store.close()
    return {"validated": len(projected), "written": written}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="reviewed projection JSONL")
    parser.add_argument("--apply", action="store_true", help="write validated records")
    args = parser.parse_args()

    stats = asyncio.run(_ingest(_records(args.input), args.apply))
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
