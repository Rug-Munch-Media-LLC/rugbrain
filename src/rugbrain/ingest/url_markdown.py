"""URL/PDF bytes -> clean markdown: markitdown first, stdlib HTML fallback.

markitdown gives tables, lists and PDF text for free; when it is unavailable or
errors on a page the fallback still yields prose rather than dropping the page.
"""

from __future__ import annotations

import io
import re
from html.parser import HTMLParser

_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "text/markdown": ".md",
    "text/plain": ".txt",
}
_SKIP = frozenset(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"])
_BLOCK = ("p", "li", "blockquote", "td", "code", "h1", "h2", "h3")


class _Blocks(HTMLParser):
    """Collect text from content blocks, dropping page chrome."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self._tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == self._tag:
            self._tag = ""

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if self._skip or not text:
            return
        if self._tag in ("h1", "h2", "h3"):
            self.out.append("#" * int(self._tag[1]) + " " + text)
        elif self._tag:
            self.out.append(text)


def fallback_markdown(body: bytes) -> str:
    """Stdlib-only prose extraction used when markitdown is missing or fails."""
    parser = _Blocks()
    parser.feed(body.decode("utf-8", "ignore"))
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(parser.out)).strip()


def to_markdown(body: bytes, content_type: str = "", url: str = "") -> str:
    """Convert fetched bytes to markdown. *url* is context for logs only."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert_stream(io.BytesIO(body), file_extension=_EXT.get(ctype, ".html"))
        markdown = (getattr(result, "text_content", "") or "").strip()
        if markdown:
            return markdown
    except Exception:
        pass
    return fallback_markdown(body)
