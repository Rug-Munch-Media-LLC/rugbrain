"""Curated news-desk channels — the outlets whose copy IS the story.

Why this list exists: ``PINNED_CHANNELS`` mirrors the bot's posting list and
``data/alt_ingest_extra_channels.txt`` mirrors the alt account's follows. Both
grew out of the operator's Telegram account, not out of journalism — so the
biggest crypto newsrooms were absent from BOTH (audited 2026-10-24: The Block,
Decrypt, Wu Blockchain, Coin Edition, ChainCatcher, 金色财经 and Milk Road were
in neither the 113-channel pinned list, nor the 135-channel extra list, nor the
892-channel runtime sweep file).

This module is that hole, closed explicitly: one row per newsroom, each row
carrying the handle AND the publisher name the handle resolves to on
``t.me/s/<handle>``. The publisher name is not decoration — it is the
provenance that the handle is a real newsroom and not a lookalike squatting a
plausible username (``dlnews`` resolves to a gaming channel; ``thecryptobasic``
resolves to an unrelated personal channel — both were REJECTED by this check).

Verification method (repeatable, no Telegram session): fetch
``https://t.me/s/<handle>`` and assert the page's channel title equals
``publisher`` and that it renders recent messages. Every row below passed on
2026-10-24 with the message count recorded in the third column.

It is a data module: no I/O, no side effects, one function.
"""

from __future__ import annotations

from typing import Final

#: ``(handle, publisher, recent_messages_seen_at_verification)``.
#: Order is by tier: English wire desks first, then specialist, then non-English.
NEWS_DESK_CHANNELS: Final[tuple[tuple[str, str, int], ...]] = (
    # ── English wire desks ───────────────────────────────────────────────
    ("theblock", "The Block", 21),
    ("decryptnews", "Decrypt", 20),
    ("wublockchainenglish", "Wu Blockchain News", 20),
    ("coinedition", "Coin Edition", 20),
    # ── English newsletter / retail desk ─────────────────────────────────
    ("milkroaddaily", "Milk Road", 7),
    # ── Non-English desks (CN) ───────────────────────────────────────────
    ("chaincatcher", "链捕手 ChainCatcher", 20),
    ("jinse", "金色财经|币圈新闻|资讯频道|行情推送", 20),
)

#: Handles only, lowercased — the shape every sweep path consumes.
NEWS_DESK_HANDLES: Final[tuple[str, ...]] = tuple(h.lower() for h, _, _ in NEWS_DESK_CHANNELS)


def resolve_news_desk(explicit: list[str] | None = None) -> list[str]:
    """Return the news-desk handles: explicit overrides, else the curated list.

    Lowercases and strips ``@`` so a caller that pastes ``@theblock`` gets the
    same string the sweep's ``t.me/s/`` URL builder expects, and de-duplicates
    in first-seen order so the same desk listed twice costs one fetch, not two.
    """
    source = explicit if explicit else list(NEWS_DESK_HANDLES)
    out: list[str] = []
    for handle in source:
        cleaned = handle.strip().lstrip("@").lower()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


__all__ = ["NEWS_DESK_CHANNELS", "NEWS_DESK_HANDLES", "resolve_news_desk"]
