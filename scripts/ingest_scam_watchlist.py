"""Monitor the rejected-channel watchlist for shill campaigns -> scam_patterns_8b.

The news sweep deliberately excludes junk/promo/flagged channels; this ingester
does the opposite: it watches exactly those channels and records the posts that
trip the shill detector (alt_ingest_shill) as scam-intel in scam_patterns_8b.
Read-only consumer — the alt account never posts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.embedder import get_embedder
from rugbrain.ingest_sources.alt_ingest_client import build_alt_ingest_client
from rugbrain.ingest_sources.alt_ingest import dialog_entities
from rugbrain.ingest_sources.alt_ingest_shill import analyze

WATCHLIST = Path(__file__).resolve().parents[1] / "data" / "alt_ingest_scam_watchlist.txt"
COLLECTION = "scam_patterns_8b"
QDRANT_URL = "http://127.0.0.1:6334"


def load_watchlist(path: Path) -> list[tuple[str, str, int]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[1], int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0))
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-per-channel", type=int, default=15)
    ap.add_argument("--max-channels", type=int, default=30)
    ap.add_argument("--min-risk", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = load_watchlist(WATCHLIST)[: args.max_channels]
    client = build_alt_ingest_client()
    await client.start()
    # Dialogs-first: the watchlist channels are all ones the account is ALREADY
    # in, so resolve them from the dialog cache — get_entity() per handle would
    # burn the ResolveUsername rate limit (8.5h flood) for nothing.
    entities = await dialog_entities(client)
    findings, scanned = [], 0
    for handle, reason, subs in rows:
        try:
            entity = entities.get(handle) or await client.get_entity(handle)
            async for msg in client.iter_messages(entity, limit=args.limit_per_channel):
                scanned += 1
                text = (msg.text or msg.message or "").strip()
                if not text:
                    continue
                sig = analyze(text, min_risk=args.min_risk)
                if sig is None:
                    continue
                findings.append({
                    "text": text[:4000],
                    "metadata": {
                        "kind": "shill_campaign",
                        "channel": handle,
                        "channel_reason": reason,
                        "channel_subscribers": subs,
                        "msg_id": int(msg.id),
                        "date": str(msg.date),
                        "risk": sig.risk,
                        "categories": list(sig.categories),
                        "reasons": list(sig.reasons),
                        "addresses": list(sig.addresses),
                        "tickers": list(sig.tickers),
                        "source": "alt_account_scam_watchlist",
                    },
                })
        except Exception as exc:  # noqa: BLE001 — one bad channel must not stop the sweep
            print(f"watch_channel_failed ch={handle} err={str(exc)[:80]}")
    await client.disconnect()

    print(f"channels={len(rows)} messages_scanned={scanned} shill_findings={len(findings)}")
    if findings and not args.dry_run:
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct

        embedder = get_embedder()
        texts = [f["text"] for f in findings]
        vectors = await embedder.embed(texts)
        qc = QdrantClient(url=QDRANT_URL, timeout=60)
        import uuid

        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"shill:{f['metadata']['channel']}:{f['metadata']['msg_id']}")),
                vector=v,
                payload=f,
            )
            for f, v in zip(findings, vectors)
        ]
        qc.upsert(collection_name=COLLECTION, points=points, wait=True)
        print(f"upserted={len(points)} into={COLLECTION}")
        worst = sorted(findings, key=lambda f: -f["metadata"]["risk"])[:5]
        for f in worst:
            m = f["metadata"]
            print(f"  risk={m['risk']} cat={','.join(m['categories'])} ch={m['channel']} :: {f['text'][:70]}")


asyncio.run(main())
