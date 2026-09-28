"""Deterministic emoji selection, with optional Telegram custom-emoji tags.

The rotation is keyed on a stable digest of the story identity (id, url,
title, timestamp) so one story always gets the same emoji on every render,
in every process, forever — Telegram and the site agree. It is NOT random:
``random`` would reshuffle between the site's SSR and the bot's send.

Telegram custom (animated) emoji are opt-in via :func:`set_emoji_ids`, which
takes a ``{plain_emoji: "custom_emoji_id"}`` map. The map is empty by
default: emoji-ids are per-account and cannot be verified from the repo, so
nothing is hardcoded. Without a mapping, :func:`render_emoji` returns the
plain emoji and non-Telegram renderers never see a ``<tg-emoji>`` tag.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping

from rugbrain.formatting.lexicon import (
    CATEGORY_EMOJI,
    DEFAULT_CATEGORY,
    SEVERITY_CATEGORY,
)

#: Rotated by :func:`set_emoji_ids`. Empty = plain emoji everywhere.
_EMOJI_IDS: dict[str, str] = {}

#: Identity fields, most stable first.
_IDENTITY_FIELDS = ("id", "story_id", "url", "title", "ts", "published_at")


def set_emoji_ids(mapping: Mapping[str, str]) -> None:
    """Install the plain→custom emoji-id map. Call once at startup.

    Args:
        mapping: ``{"🚨": "5368324170671202286", ...}``. Only ids the
            account owner has verified are supplied; an unknown id would
            make Telegram reject the whole message.
    """
    global _EMOJI_IDS
    _EMOJI_IDS = {str(k): str(v) for k, v in mapping.items() if v}


def resolve_category(story: Mapping[str, object]) -> str:
    """Canonical category key for a story, falling back to severity."""
    from rugbrain.formatting.lexicon import CATEGORY_ALIASES

    raw = str(story.get("category") or story.get("kind") or "").strip().lower()
    if not raw:
        severity = str(story.get("severity") or "").strip().lower()
        return SEVERITY_CATEGORY.get(severity, DEFAULT_CATEGORY)
    if raw in CATEGORY_EMOJI:
        return raw
    return CATEGORY_ALIASES.get(raw, DEFAULT_CATEGORY)


def _identity(story: Mapping[str, object]) -> str:
    for field in _IDENTITY_FIELDS:
        value = story.get(field)
        if value:
            return f"{field}={value}"
    return f"title={story.get('title', '')}"


def pick_emoji(story: Mapping[str, object]) -> str:
    """The plain emoji for this story — stable per story, themed by category."""
    pool = CATEGORY_EMOJI[resolve_category(story)]
    digest = hashlib.blake2b(_identity(story).encode("utf-8"), digest_size=4).digest()
    return pool[int.from_bytes(digest, "big") % len(pool)]


def render_emoji(story: Mapping[str, object]) -> str:
    """The emoji as Telegram markup when an id is mapped, else the plain char.

    Returns an empty string when custom-emoji tags are enabled but the
    selected emoji has no id — the caller then gets no marker at all rather
    than a broken tag.
    """
    emoji = pick_emoji(story)
    if not _EMOJI_IDS:
        return emoji
    emoji_id = _EMOJI_IDS.get(emoji)
    if not emoji_id:
        return emoji
    return f'<tg-emoji emoji-id="{emoji_id}">{emoji}</tg-emoji>'


def configured_from_env() -> bool:
    """True when the env-configured custom-emoji map was installed."""
    return bool(os.getenv("RAG_TG_EMOJI_IDS", "").strip())


def load_emoji_ids_from_env(env_var: str = "RAG_TG_EMOJI_IDS") -> int:
    """Install the custom-emoji map from ``emoji=id`` pairs in an env var.

    Accepts either a JSON object or ``🚨=5368...,💀=5368...`` (comma or
    newline separated). Returns the number of ids installed; 0 when the var
    is unset or malformed. Never raises — a bad map degrades to plain emoji.
    """
    import json

    raw = os.getenv(env_var, "").strip()
    if not raw:
        return 0
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        set_emoji_ids(parsed)
        return len(parsed)
    pairs: dict[str, str] = {}
    for chunk in raw.replace("\n", ",").split(","):
        emoji, sep, custom_id = chunk.partition("=")
        if sep and emoji.strip() and custom_id.strip():
            pairs[emoji.strip()] = custom_id.strip()
    set_emoji_ids(pairs)
    return len(pairs)
