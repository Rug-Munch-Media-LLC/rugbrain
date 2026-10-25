"""Corpus lane probe — ONE valid 4096-d lane, or abort loudly.

The ingest path must never write to a lane that is not the 4096-d corpus lane,
and must never silently fall back to a 1024/2560-d lane. A lane that answers
HTTP 401 or returns a short vector is a hard stop: we name the lane and why.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CANONICAL_DIM = 4096


class LaneUnavailable(RuntimeError):
    """No corpus-valid embedding lane. Carries the lane name and the reason."""


async def assert_corpus_lane(embedder, lane: str, sample: str = "lane probe") -> int:
    """Return the embedding dim, or raise ``LaneUnavailable`` with lane + reason."""
    try:
        vec = await embedder.embed_one(sample)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim, never swallowed
        raise LaneUnavailable(f"lane={lane} reason=embed_failed err={exc}") from exc
    if not vec:
        raise LaneUnavailable(f"lane={lane} reason=empty_vector")
    dim = len(vec)
    if dim != CANONICAL_DIM:
        raise LaneUnavailable(
            f"lane={lane} reason=wrong_dim dim={dim} expected={CANONICAL_DIM}"
        )
    logger.info("corpus_lane_ok lane=%s dim=%d", lane, dim)
    print(f"corpus_lane_ok lane={lane} dim={dim}")
    return dim
