"""The write path: embed the diff into the twin, upserting the base's own ids.

IDEMPOTENCE AND RESTART SAFETY rest on one choice: a point is written under
the BASE'S OWN POINT ID, not a freshly derived one. Re-running therefore
finds an empty diff and embeds nothing, and a crash mid-run leaves a
partially-filled twin that the next run completes — it never duplicates,
because upsert on the same id replaces rather than adds.

SPARSE: codebase_reference_v2_8b is dense+sparse, and the base carries a
sparse vector per point (measured 2026-09-29: 93 indices on one sampled point,
23 on the next, BYTE-IDENTICAL to the twin's for the same id). Writing dense
only would leave 4,644 points invisible to the hybrid RRF path, so the base's
own sparse vector is carried across unchanged. The base already has it — it
needs no re-derivation, and the same-shape twins created for the three
no-twin collections are dense-only because their bases are.

UNRECOVERABLE points are counted, never embedded: there is no prose to embed
(see ``embed.embed_text``). A point whose payload offers no provable text is
skipped and surfaced, so the twin can be short by an EXPLAINED amount instead
of a silent one.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from . import qdrant_io as q
from .embed import embed_batch, embed_text
from .plan import Report

logger = logging.getLogger("eliminate_4b.fill")

BATCH = 64  # measured optimum: 39.4 pt/s vs 28.8 @32 and 14.2 @128
UPSERT_BATCH = 128


def _provenance(base: str) -> dict[str, Any]:
    """The four keys the existing twins already carry (measured on
    security_feeds_8b). Matching them keeps the twin self-describing."""
    from .embed import CANONICAL_DIM, DEEPINFRA_MODEL

    return {
        "embedding_model": DEEPINFRA_MODEL,
        "embedding_dim": CANONICAL_DIM,
        "reembed_lane": "deepinfra",
        "reembedded_from": base,
    }


def fill(c: httpx.Client, h: httpx.Client, rep: Report, key: str, limit: int | None = None) -> None:
    """Embed the missing ids into rep.twin, then re-measure for verification."""
    twin_info = q.collection_info(c, rep.twin)
    assert twin_info is not None, "twin vanished between plan and fill"
    _, vec_name = q.vector_shape(twin_info)
    copy_sparse = q.has_sparse(twin_info)

    twin_ids = q.twin_id_set(c, rep.twin)
    started, done, skipped = time.monotonic(), 0, 0
    pending: list[tuple[str, str, dict[str, Any], Any]] = []

    def flush() -> None:
        nonlocal done, pending
        if not pending:
            return
        vecs = embed_batch(h, [t for _, t, _, _ in pending], key)
        points = []
        for (pid, _text, payload, sparse), vec in zip(pending, vecs, strict=True):
            v: dict[str, Any] = {}
            if vec_name:
                v[vec_name] = vec
            else:
                v[""] = vec
            if copy_sparse:
                v["sparse"] = sparse
            points.append({"id": pid, "vector": v, "payload": payload})
        for i in range(0, len(points), UPSERT_BATCH):
            chunk = points[i : i + UPSERT_BATCH]
            q.upsert(c, rep.twin, chunk)
            done += len(chunk)
        if done and done % (BATCH * 10) < BATCH:
            el = time.monotonic() - started
            logger.info(
                "  %s: %d embedded, %.1f pt/s, %d left",
                rep.base,
                done,
                done / el if el else 0,
                rep.to_embed - done,
            )
        pending = []

    for page in q.scroll(c, rep.base, with_payload=True, with_vector=copy_sparse):
        for p in page:
            pid = str(p["id"])
            if pid in twin_ids:
                continue
            payload = dict(p.get("payload") or {})
            text, source = embed_text(payload)
            if not text:
                skipped += 1
                continue
            if source == "derived":
                # Auditable: a reader can tell this text was rebuilt from
                # payload fields and sha1-proved against content_hash, not
                # read from a text field.
                payload["text_source"] = "derived_from_content_hash"
            payload.update(_provenance(rep.base))
            sparse = (p.get("vector") or {}).get("sparse") or {"indices": [], "values": []}
            pending.append((pid, text, payload, sparse))
            if len(pending) >= BATCH:
                flush()
                if limit and done >= limit:
                    return _finish(c, rep, started, done, skipped)
        # The limit can also fall INSIDE a page, before the first flush
        # boundary (BATCH is 64, a page is 512). Honour it there, or a
        # "--limit 3" smoke test silently embeds a whole page.
        if limit and done + len(pending) >= limit:
            del pending[limit - done :]
            flush()
            return _finish(c, rep, started, done, skipped)
    flush()
    _finish(c, rep, started, done, skipped)


def _finish(c: httpx.Client, rep: Report, started: float, done: int, skipped: int = 0) -> None:
    rep.seconds = time.monotonic() - started
    rep.embedded = done
    rep.unrecoverable = skipped
    rep.twin_after = q.count(c, rep.twin)
    # Parity is the operator's bar, and it is checked here rather than left to
    # the reader of a table. A shortfall is FAILED with its cause attached.
    if not rep.parity:
        cause = (
            f"{rep.unrecoverable} point(s) had no recoverable embed input"
            if rep.unrecoverable
            else "unknown shortfall"
        )
        rep.status = "FAILED"
        rep.failed.append(
            f"twin holds {rep.twin_after}, base holds {rep.base_count} "
            f"(needs {rep.base_count}); base left in place, do not delete"
        )
        rep.failed.append(f"shortfall cause: {cause}")
