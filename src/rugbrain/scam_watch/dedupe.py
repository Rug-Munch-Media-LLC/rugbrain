"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Brief dedupe. Two different videos about the same token by the same channel are
one story (repeat), not two; the same video is never briefed twice. Repeat
offenders are marked on the brief for the editor, not auto-published.
"""

from __future__ import annotations

from collections.abc import Sequence

from rugbrain.scam_watch.models import ArticleBrief


def dedupe_brief(existing: Sequence[ArticleBrief], candidate: ArticleBrief) -> ArticleBrief | None:
    """Return the brief to keep, or ``None`` when it duplicates a prior video."""
    if any(item.video_id == candidate.video_id for item in existing):
        return None
    same_channel = [item for item in existing if item.handle.lower() == candidate.handle.lower()]
    prior = len(same_channel)
    if not prior:
        return candidate
    return candidate.model_copy(update={"repeat_offender": True, "prior_reports": prior})
