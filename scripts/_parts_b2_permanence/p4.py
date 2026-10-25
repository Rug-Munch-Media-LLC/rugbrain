"""Part 4/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F403


def _merge_shard_progress(
    prior: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Never lose shards already in B2; derive `partial` from the arithmetic.

    MEASURED DEFECT (2026-10-24, three consecutive firings): rag_documents
    landed 8 shards at 03:00, 10 at 03:40 and 3 at 04:20 -- each run restarted
    at part-00000 and the manifest entry was REPLACED by the newer, smaller
    shard list. The day's count therefore never advanced, every firing
    re-exported ~10 x 611 MB, and the collection could never complete.

    The rule is monotonic by construction:
      * shards are unioned by object_key (a re-export of the same part cannot
        shrink the list, and a genuinely new part is added);
      * expected_shards is the max of both sides (never lowered);
      * `partial` is derived from len(shards) vs expected_shards, so a caller
        that forgets the flag can no longer advertise a half-exported
        collection as done.
    """
    if not incoming:
        return incoming
    prior_shards = list((prior or {}).get("shards") or [])
    incoming_shards = list(incoming.get("shards") or [])
    if not prior_shards and not incoming_shards:
        return incoming
    merged: dict[str, dict[str, Any]] = {}
    for shard in prior_shards + incoming_shards:
        key = shard.get("object_key") if isinstance(shard, dict) else None
        if key:
            merged[str(key)] = shard
    expected = max(
        int((prior or {}).get("expected_shards") or 0),
        int(incoming.get("expected_shards") or 0),
    )
    out = dict(incoming)
    out["shards"] = [merged[k] for k in sorted(merged)]
    out["point_count"] = sum(int(s.get("point_count", 0) or 0) for s in out["shards"])
    out["bytes_stored"] = sum(int(s.get("bytes_stored", 0) or 0) for s in out["shards"])
    if expected:
        out["expected_shards"] = expected
    if expected and len(out["shards"]) >= expected:
        out.pop("partial", None)
        out.pop("deferred", None)
    else:
        out["partial"] = True
    return out


__all__ = [
    "_merge_shard_progress",
]
