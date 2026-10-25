"""The transcript fallback order, declared once.

Captions are the fragile half of YouTube ingest. The order is explicit and each
rung names itself in ``transcript_status`` so a stored item says which path
produced its text — a real caption is distinguishable from a description blurb.

1. ``captions:yt-dlp`` — already installed, handles most public subtitles.
2. ``captions:youtube-transcript-api`` — pure Python, no binary, no key.
3. ``metadata:description-only`` — always available, never invented: the video
   is stored with its public description and an honest status instead of being
   dropped for lacking captions.
"""

from __future__ import annotations

__all__ = ["DESCRIPTION_ONLY", "STRATEGIES", "transcript_plan"]

DESCRIPTION_ONLY = "metadata:description-only"
STRATEGIES = (
    "captions:yt-dlp",
    "captions:youtube-transcript-api",
    DESCRIPTION_ONLY,
)


def transcript_plan(*, yt_dlp_available: bool, transcript_api_available: bool) -> tuple[str, ...]:
    """Ordered rungs to attempt for one video, ending at description-only."""
    plan = [STRATEGIES[0]] if yt_dlp_available else []
    if transcript_api_available:
        plan.append(STRATEGIES[1])
    plan.append(DESCRIPTION_ONLY)
    return tuple(plan)
