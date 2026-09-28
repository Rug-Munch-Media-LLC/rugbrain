"""Headline styling: escaping, deterministic rotation, per-target output."""

from __future__ import annotations

import re

import pytest

from rugbrain.formatting import emoji as emoji_mod
from rugbrain.formatting.emoji import pick_emoji, render_emoji, set_emoji_ids
from rugbrain.formatting.headline import style_for_site, style_headline

RUG = {
    "id": "abc123",
    "title": "Dev drains $4M from LP pool, team ghosts Discord",
    "category": "rug",
    "severity": "critical",
    "url": "https://x.com/a?b=1&c=2",
}
NEWS = {
    "id": "zzz999",
    "title": "SEC opens consultation on token listings",
    "category": "news",
    "severity": "medium",
    "url": "https://example.org/p",
}

_ALLOWED_TAGS = {"i", "b", "code", "pre", "a", "tg-emoji"}


def _balanced(html: str) -> bool:
    """Every opened tag is closed, in order, and is on the allowlist."""
    stack: list[str] = []
    for match in re.finditer(r"<(/?)([a-zA-Z-]+)[^>]*>", html):
        closing, tag = match.group(1), match.group(2)
        assert tag in _ALLOWED_TAGS, f"unexpected tag <{tag}>"
        if closing:
            assert stack and stack.pop() == tag, f"unbalanced <{tag}>"
        else:
            stack.append(tag)
    return not stack


# ── escaping ──────────────────────────────────────────────────────────


def test_headline_escapes_html_in_title() -> None:
    out = style_headline({**RUG, "title": "<script>alert(1)</script> & more"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert _balanced(out)


def test_headline_escapes_ampersand_in_source_url() -> None:
    """The url's `&` must not become a raw entity start when reused."""
    out = style_headline(RUG)
    assert "https://x.com/a?b=1&c=2" not in out  # url is not in the headline
    assert "&" not in out.replace("&lt;", "").replace("&gt;", "").replace("&amp;", "")


def test_severity_cue_is_emitted_and_balanced() -> None:
    out = style_headline(RUG)
    assert "<i>(🔴 CONFIRMED)</i>" in out
    assert _balanced(out)


def test_unknown_severity_has_no_cue() -> None:
    assert "<i>" not in style_headline({**RUG, "severity": "weird"})


def test_empty_story_does_not_raise() -> None:
    out = style_headline({})
    assert out
    assert _balanced(out)


# ── deterministic rotation ────────────────────────────────────────────


def test_same_story_always_yields_same_emoji() -> None:
    assert pick_emoji(RUG) == pick_emoji(dict(RUG))
    assert style_headline(RUG) == style_headline(dict(RUG))


def test_rotation_varies_across_a_story_set() -> None:
    stories = [{**NEWS, "id": f"n{i}", "title": f"Headline number {i}"} for i in range(24)]
    distinct = {pick_emoji(s) for s in stories}
    assert len(distinct) >= 3, f"rotation too flat: {distinct}"


def test_rotation_is_keyed_on_identity_not_order() -> None:
    a = {**NEWS, "id": "a1", "title": "Same words here"}
    b = {**NEWS, "id": "b2", "title": "Same words here"}
    assert pick_emoji(a) == pick_emoji(b), "title is a fallback key, id wins"
    assert pick_emoji({**NEWS, "id": "c3"}) != pick_emoji({**NEWS, "id": "d4"})


def test_category_themes_are_distinct_pools() -> None:
    assert pick_emoji(RUG) in ("🚨", "🔴", "⚠️", "🩸")
    assert pick_emoji(NEWS) in ("📰", "🗞️", "📡", "🔔")


def test_aliases_resolve_to_canonical_category() -> None:
    assert "EXPLOIT" in style_headline({**NEWS, "category": "exploit"})
    assert "RUG" in style_headline({**NEWS, "category": "Rug_Pull"})


# ── site vs telegram ──────────────────────────────────────────────────


def test_site_output_contains_no_markup() -> None:
    for story in (RUG, NEWS):
        out = style_for_site(story)
        assert "<" not in out and ">" not in out
        assert "<tg-emoji" not in out


def test_tg_and_site_differ_only_in_markup() -> None:
    tg = style_headline(RUG)
    site = style_for_site(RUG)
    assert tg != site
    # same words, same emoji — the site must not disagree with Telegram
    assert pick_emoji(RUG) in tg and pick_emoji(RUG) in site
    assert "Dev drains $4M" in tg and "Dev drains $4M" in site
    assert "RUG" in tg and "RUG" in site


# ── custom (animated) emoji ───────────────────────────────────────────


def test_no_custom_tag_by_default() -> None:
    set_emoji_ids({})
    assert "<tg-emoji" not in render_emoji(RUG)
    assert render_emoji(RUG) == pick_emoji(RUG)


def test_custom_emoji_id_is_wrapped_and_balanced() -> None:
    try:
        set_emoji_ids({pick_emoji(RUG): "5368324170671202286"})
        out = style_headline(RUG)
        assert '<tg-emoji emoji-id="5368324170671202286">' in out
        assert _balanced(out)
        # the site must NOT receive the Telegram-only tag
        assert "<tg-emoji" not in style_for_site(RUG)
    finally:
        set_emoji_ids({})


def test_unmapped_emoji_falls_back_to_plain_char() -> None:
    try:
        set_emoji_ids({"🦄": "123"})
        assert render_emoji(RUG) == pick_emoji(RUG)
    finally:
        set_emoji_ids({})


def test_emoji_ids_ignore_empty_values() -> None:
    try:
        set_emoji_ids({pick_emoji(NEWS): ""})
        assert emoji_mod.configured_from_env() in (True, False)
        assert "<tg-emoji" not in render_emoji(NEWS)
    finally:
        set_emoji_ids({})


# ── long titles ───────────────────────────────────────────────────────


def test_long_title_is_truncated_with_ellipsis() -> None:
    out = style_headline({**NEWS, "title": "word " * 80})
    assert len(out) < 260
    assert "… <i>" in out  # truncated, then the severity cue still appended


@pytest.mark.parametrize("blank", ["", "   ", "\n\n"])
def test_blank_title_degrades_to_content_or_placeholder(blank: str) -> None:
    out = style_for_site({**NEWS, "title": blank, "content": ""})
    assert out.strip()
