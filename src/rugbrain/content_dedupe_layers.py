"""The three duplicate layers, and the evidence each one produces.

One layer is never enough, and the ORDER is the point:

1. **exact** — SHA-256 over the normalised title+body. Cheap, no false
   positives, catches the copy-paste syndication that is most of the traffic.
2. **near** — token-shingle Jaccard (reused from the ingest detector). Tolerates
   the light edits syndicators make: a leading emoji, a changed ticker, a link.
3. **semantic** — cosine on the CORPUS embed lane (Qwen3-Embedding-8B Q8_0,
   ``:8004``, 4096d), which is the only layer that sees a paraphrase.

The semantic threshold is a MEASUREMENT, not a taste. Measured 2026-10-28 on the
live lane over the 12-item fixture set in
``tests/fixtures/content/value_fixtures.json``:

    same-story pairs      min cosine 0.6238   (cross-outlet paraphrase)
    different-story pairs max cosine 0.5500

The usable gap is [0.5500, 0.6238]; :data:`SEMANTIC_THRESHOLD` is its midpoint,
so the constant carries ~0.04 of margin on BOTH sides. Re-calibrate whenever the
embedder or the collection changes and quote those two numbers when you do — a
threshold without its measured margins is a guess wearing a constant's name.

Cosines are passed IN (:mod:`rugbrain.content_dedupe_layers` does no I/O), so the
comparison logic is testable without a network lane.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Final

from rugbrain.ingest_sources.ingest_dedupe import (
    DUP_THRESHOLD,
    jaccard,
    normalize_for_dedupe,
    shingles,
)

EXACT: Final = "exact"
NEAR: Final = "near"
SEMANTIC: Final = "semantic"
#: Layer names, strongest first — the order layers are tried in.
LAYERS: Final = (EXACT, NEAR, SEMANTIC)

#: Calibrated on the 8B/4096 corpus lane; see the module docstring.
SEMANTIC_THRESHOLD: Final = 0.59
#: Near-duplicate bar, shared with the ingest detector so one constant decides.
NEAR_THRESHOLD: Final = DUP_THRESHOLD

#: Identity does not need the tail of a long body; hashing stays cheap.
MAX_HASH_CHARS: Final = 4000


def story_text(story: dict[str, Any]) -> str:
    """Normalised title+body — the string every layer agrees on."""
    title = str(story.get("title") or "")
    body = str(story.get("content") or "")
    return normalize_for_dedupe(f"{title}\n{body}".strip())[:MAX_HASH_CHARS]


def exact_key(story: dict[str, Any]) -> str:
    """Layer (a): SHA-256 of the normalised title+body."""
    return hashlib.sha256(story_text(story).encode("utf-8")).hexdigest()


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity of two embeddings; 0.0 for an unusable pair."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / (norm + 1e-12)


def layers_hit(
    story: dict[str, Any],
    other: dict[str, Any],
    *,
    cosine_value: float | None = None,
    near_threshold: float = NEAR_THRESHOLD,
    semantic_threshold: float = SEMANTIC_THRESHOLD,
) -> dict[str, Any]:
    """Which layer, if any, says these two are the same story.

    Returns ``{"layer": "exact"|"near"|"semantic"|None, "exact": bool,
    "jaccard": float, "cosine": float|None, "similarity": float}``. A layer
    only reports when it is CONFIDENT: an exact hash match is 1.0, a Jaccard
    over the bar reports its Jaccard, a cosine over its calibrated bar reports
    its cosine. ``similarity`` is the strongest evidence seen, whether or not
    it cleared a bar — that is what the value score needs.
    """
    if exact_key(story) == exact_key(other):
        return {"layer": EXACT, "exact": True, "jaccard": 1.0, "cosine": None, "similarity": 1.0}
    score = jaccard(shingles(story_text(story)), shingles(story_text(other)))
    if score >= near_threshold:
        return {
            "layer": NEAR,
            "exact": False,
            "jaccard": round(score, 6),
            "cosine": cosine_value,
            "similarity": round(score, 6),
        }
    if cosine_value is not None and cosine_value >= semantic_threshold:
        return {
            "layer": SEMANTIC,
            "exact": False,
            "jaccard": round(score, 6),
            "cosine": round(cosine_value, 6),
            "similarity": round(cosine_value, 6),
        }
    strongest = max(score, cosine_value or 0.0)
    return {
        "layer": None,
        "exact": False,
        "jaccard": round(score, 6),
        "cosine": None if cosine_value is None else round(cosine_value, 6),
        "similarity": round(strongest, 6),
    }


__all__ = [
    "EXACT",
    "LAYERS",
    "MAX_HASH_CHARS",
    "NEAR",
    "NEAR_THRESHOLD",
    "SEMANTIC",
    "SEMANTIC_THRESHOLD",
    "cosine",
    "exact_key",
    "layers_hit",
    "story_text",
]
