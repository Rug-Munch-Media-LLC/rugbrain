"""Headline rendering for the two consumers we actually have.

``style_headline`` targets Telegram (``parse_mode=HTML``, per
rugmunchbot's ``_posts_rag/_approval.py``) and therefore escapes ``& < >``
and may carry ``<tg-emoji>`` custom-emoji tags. ``style_for_site`` targets
plain text: the API JSON and the site's news section render the value as a
string, so it must contain no markup at all — a literal ``<tg-emoji ...>``
would show up as text on the page.

Both share the same category label, title and severity cue, so the two
renderings only ever differ in markup, never in meaning.
"""

from __future__ import annotations

from collections.abc import Mapping

from rugbrain.formatting.emoji import render_emoji
from rugbrain.formatting.lexicon import CATEGORY_PREFIX, SEVERITY_CUE

MAX_TITLE = 140


def _esc(text: str) -> str:
    """Telegram-HTML escape. Only these three chars are special."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title(story: Mapping[str, object]) -> str:
    raw = str(story.get("title") or story.get("content") or "").strip()
    raw = " ".join(raw.split())
    if len(raw) > MAX_TITLE:
        cut = raw[:MAX_TITLE].rsplit(" ", 1)[0]
        raw = (cut or raw[:MAX_TITLE]) + "…"
    return raw or "Untitled story"


def _parts(story: Mapping[str, object]) -> tuple[str, str, str]:
    """(category label, title, severity cue) — shared by both renderers."""
    from rugbrain.formatting.emoji import resolve_category

    label = CATEGORY_PREFIX[resolve_category(story)]
    severity = str(story.get("severity") or "").strip().lower()
    return label, _title(story), SEVERITY_CUE.get(severity, "")


def style_headline(story: Mapping[str, object], *, emoji: str | None = None) -> str:
    """Telegram-HTML headline: emoji, category label, title, credibility cue.

    Args:
        story: the story mapping (any of the Qdrant payload shapes).
        emoji: pre-rendered marker, defaults to the rotated one. Injectable
            so tests and callers can pin the animation source.

    Returns:
        HTML-safe text. No ``<``/``&`` from the title can escape, so an
        existing ``Source: <a>`` link in the post body is never broken.
    """
    label, title, cue = _parts(story)
    marker = render_emoji(story) if emoji is None else emoji
    # The marker is markup we generated (possibly a <tg-emoji> tag) and must
    # NOT be escaped; the label and title come from the story payload and
    # always are.
    head = f"{marker} {_esc(f'{label} {title}')}".strip() if marker else _esc(f"{label} {title}")
    return head + (f" <i>({_esc(cue)})</i>" if cue else "")


def style_for_site(story: Mapping[str, object], *, emoji: str | None = None) -> str:
    """Plain-text headline for the site/API. Never contains markup.

    The plain emoji is always used — a ``<tg-emoji>`` tag has no meaning in
    HTML and would leak into the page as literal text.
    """
    from rugbrain.formatting.emoji import pick_emoji

    label, title, cue = _parts(story)
    marker = pick_emoji(story) if emoji is None else emoji
    head = " ".join(p for p in (marker, label, title) if p)
    return f"{head} ({cue})" if cue else head
