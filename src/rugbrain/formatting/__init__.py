"""Presentation formatting for stories (Telegram + web).

Pure, dependency-free helpers that turn a stored story into a headline.
Two render targets:

    style_headline(story)   Telegram HTML — category prefix, title, cue
    style_for_site(story)   plain text — same words, no markup, no tags

Both pick the SAME emoji for the same story (see :mod:`.emoji`): the site
and the Telegram channel must never disagree about how a story is labelled.
"""

from __future__ import annotations

from rugbrain.formatting.emoji import (
    load_emoji_ids_from_env,
    pick_emoji,
    render_emoji,
    set_emoji_ids,
)
from rugbrain.formatting.headline import style_for_site, style_headline
from rugbrain.formatting.lexicon import CATEGORY_PREFIX, SEVERITY_CUE

__all__ = [
    "CATEGORY_PREFIX",
    "SEVERITY_CUE",
    "load_emoji_ids_from_env",
    "pick_emoji",
    "render_emoji",
    "set_emoji_ids",
    "style_for_site",
    "style_headline",
]

# Install account-specific custom (animated) emoji ids once, on package load.
# A no-op unless RAG_TG_EMOJI_IDS is set; degrades to plain emoji otherwise.
load_emoji_ids_from_env()
