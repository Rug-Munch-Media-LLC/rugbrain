"""Vector normalisation — the one place a dim mismatch is decided.

Silent zero-pad/truncate masked provider contract drift (a 1024-dim vector
silently padded into a 2560-dim index poisons retrieval). This module is the
only normaliser any embedding lane may use, and it REJECTS rather than
coerces, so a drift becomes a named exception instead of a wrong ranking.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


def resize(vec: list[float], dim: int) -> list[float]:
    """L2-normalize a vector; REJECT dim mismatches (TOP-100 #25).

    Args:
        vec: Raw provider vector.
        dim: The expected width. A mismatch raises — see EMBEDDING_DIM in
            :mod:`.config`: the canonical dim is 4096 and a 2560 index is a
            retained retired generation this code must not write to.

    Returns:
        The unit-length vector as a plain list of floats (JSON-serializable
        for Redis).

    Raises:
        ValueError: On any width other than ``dim``.
    """
    if len(vec) != dim:
        log.error("embedding_dim_mismatch got=%d want=%d", len(vec), dim)
        raise ValueError(f"embedding dim mismatch: got {len(vec)}, expected {dim}")
    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n > 0:
        arr = arr / n
    return [float(x) for x in arr]


def truncate_mrl(vec: list[float], dim: int) -> list[float] | None:
    """Matryoshka truncate-down to ``dim`` and renormalise; None if too short.

    A provider may legitimately return MORE than the target width (DeepInfra's
    8B, the NIM 2048d models). Truncating down keeps the index-compatible
    prefix. A vector NARROWER than ``dim`` is a contract violation and returns
    None so the lane chain can fall through — :func:`resize` is the authority
    that rejects it.

    Returns:
        The unit-length truncated vector, or None when ``len(vec) < dim``.
    """
    if len(vec) < dim:
        return None
    cut = vec[:dim]
    norm = sum(x * x for x in cut) ** 0.5
    return [x / norm for x in cut] if norm else cut
