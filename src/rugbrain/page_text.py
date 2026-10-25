"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

HTML -> readable markdown, with the chrome removed *before* chunking.

The defect this closes: a fetcher that keeps every text node stores the SPA
bootstrap (``window.__reactRouterContext``, GTM, Cloudflare challenge) as if it
were prose. ``extract_page_text`` walks the document once and emits only
headings, paragraphs, list items, quotes and pre-formatted blocks; script,
style, nav, header, footer, aside, form, noscript and svg are skipped whole,
including their text nodes.

Stdlib ``HTMLParser`` only — same constraint (and the same shape) as
:mod:`rugbrain.article_fetch`, no readability/markitdown dependency at runtime.
A page that yields nothing is refused upstream as ``blocked.render``; this
module never invents text.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Final

SKIP: Final = frozenset(
    {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg", "iframe"}
)
_BLOCK_TAGS: Final = {"p": "", "li": "- ", "blockquote": "> ", "pre": "```\n"}
_HEADINGS: Final = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### "}


class _Markdown(HTMLParser):
    """Collect block text as markdown; skip chrome subtrees entirely."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._skip = 0
        self._prefix = ""
        self._buf: list[str] = []
        self._capturing = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP:
            self._skip += 1
            return
        if self._capturing:
            return
        if tag in _HEADINGS or tag in _BLOCK_TAGS:
            self._capturing, self._buf = True, []
            self._prefix = _HEADINGS.get(tag, _BLOCK_TAGS.get(tag, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if not self._capturing or (tag not in _HEADINGS and tag not in _BLOCK_TAGS):
            return
        text = " ".join("".join(self._buf).split())
        minimum = 2 if self._prefix.startswith("#") else 6
        if text and len(text.split()) >= minimum:
            self.blocks.append(self._prefix + text)
        self._capturing, self._buf, self._prefix = False, [], ""

    def handle_data(self, data: str) -> None:
        if self._capturing and not self._skip:
            self._buf.append(data)


def extract_page_text(markup: str) -> str:
    """Return markdown prose for ``markup``; empty string when it is a shell."""
    if not markup or "<" not in markup:
        return ""
    parser = _Markdown()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # malformed markup is a refusal, never a crash
        return ""
    return "\n\n".join(parser.blocks)
