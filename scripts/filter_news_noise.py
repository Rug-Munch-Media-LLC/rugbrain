"""filter_news_noise.py — classify + remove non-crypto noise from news collections.

Measured 2026-09-20: ``news_v2`` is 143,988 points, of which **102,606 (71%)**
sit in the ``general`` bucket — generic Telegram messages including Persian /
Iranian news channels (``t.me/IranintlTV``, ``khabarfuri``, ``farsna``, …)
that have nothing to do with crypto intelligence.

Classification (documented, testable):

* ``topical``  — payload ``metadata.category`` is a crypto category
  (news / crypto / market / shiller / ethereum / bitcoin / solana / tron /
  data / x_social / security / scam_caller) → always kept.
* ``crypto-general`` — ``general``/unknown category BUT the content carries a
  crypto signal (contract address, ``$TICKER``, or a crypto keyword) → kept.
* ``noise`` — general/unknown category with **no** crypto signal → removable.

Usage:
    python3 scripts/filter_news_noise.py --report              # counts + samples
    python3 scripts/filter_news_noise.py --tag                 # payload noise=true
    python3 scripts/filter_news_noise.py --delete              # delete noise points
    python3 scripts/filter_news_noise.py --collection social_intel --report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter

from rugbrain.news_filter import classify_payload

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")

def classify(payload: dict) -> str:
    """Delegate to the shared classifier (rugbrain.news_filter)."""
    return classify_payload(payload)


def scroll_all(collection: str, batch: int = 2000):
    offset = None
    while True:
        body: dict = {
            "limit": batch,
            "with_payload": ["content", "metadata.category", "metadata.title", "category"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        req = urllib.request.Request(  # noqa: S310 — Qdrant http://, audited 2026-09-20
            f"{QDRANT}/collections/{collection}/points/scroll",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 — Qdrant http://, audited 2026-09-20
            res = json.load(resp)["result"]
        points = res.get("points") or []
        if not points:
            return
        for pt in points:
            yield str(pt["id"]), pt.get("payload") or {}
        offset = res.get("next_page_offset")
        if offset is None:
            return


def _post(path: str, body: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        f"{QDRANT}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — Qdrant http://, audited 2026-09-20
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="news_v2")
    ap.add_argument("--report", action="store_true", help="counts + samples only (default)")
    ap.add_argument("--tag", action="store_true", help="set payload noise=true on noise points")
    ap.add_argument("--delete", action="store_true", help="delete noise points")
    args = ap.parse_args()

    counts: Counter[str] = Counter()
    samples: dict[str, str] = {}
    noise_ids: list[str] = []
    for pid, payload in scroll_all(args.collection):
        kind = classify(payload)
        counts[kind] += 1
        if kind not in samples:
            samples[kind] = (payload.get("content") or "")[:110].replace("\n", " ")
        if kind == "noise":
            noise_ids.append(pid)

    total = sum(counts.values())
    print(f"collection {args.collection}: {total:,} points")
    for kind, n in counts.most_common():
        pct = 100 * n / total if total else 0
        print(f"  {kind:15} {n:>9,}  ({pct:5.1f}%)  e.g. {samples.get(kind, '')!r}")

    if args.tag and noise_ids:
        for i in range(0, len(noise_ids), 500):
            _post(
                f"/collections/{args.collection}/points/payload?wait=true",
                {
                    "payload": {"noise": True, "noise_reason": "non-crypto general"},
                    "points": noise_ids[i : i + 500],
                },
            )
        print(f"tagged {len(noise_ids):,} noise points (noise=true)")
    if args.delete and noise_ids:
        for i in range(0, len(noise_ids), 500):
            _post(
                f"/collections/{args.collection}/points/delete?wait=true",
                {"points": noise_ids[i : i + 500]},
            )
        print(f"deleted {len(noise_ids):,} noise points")
    if not (args.tag or args.delete):
        print("\n(report only — pass --tag and/or --delete to act)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
