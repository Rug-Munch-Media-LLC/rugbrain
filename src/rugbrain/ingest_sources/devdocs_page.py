"""Turn one fetched documentation page into a corpus document.

One job: given a declared :class:`~rugbrain.ingest_sources.devdocs_specs.DocSite` and the
bytes its URL returned, produce a clean, deterministically-identified
:class:`~rugbrain.ingest_sources.base.IngestDoc` — or ``None`` when the page carries no
usable text.

WHY A STRIPPER AND NOT A PARSER
    Most of the manifest points at raw ``.md``/``.rst`` (they arrive clean). The remainder
    are HTML doc pages whose navigation, sidebars and cookie banners are noise. A full
    HTML parser is a new dependency and a new failure surface for a payload we are going
    to chunk anyway; a bounded tag strip is sufficient and honest about being lossy. The
    strip is deliberately conservative: it removes ``script``/``style``/``nav``/``footer``
    blocks wholesale, keeps text inside ``main``/``article`` when those wrappers exist,
    and collapses whitespace.

MINIMUM LENGTH
    A stripped page shorter than :data:`MIN_CONTENT_CHARS` is a redirect stub, a cookie
    wall or a 200-with-an-error-page. Emitting it would put noise in front of the
    embedder and inflate the count of a run that actually failed — so ``None`` is
    returned and the caller records a skip rather than a document.
"""

from __future__ import annotations

import hashlib
import html as html_module
import re

from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.devdocs_specs import DocSite

#: Below this, the page is a stub (redirect, consent wall, error page), not a document.
MIN_CONTENT_CHARS = 400

_NOISE_BLOCK_RE = re.compile(
    r"<(script|style|nav|footer|noscript|svg)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_MAIN_BLOCK_RE = re.compile(
    r"<(main|article)\b[^>]*>(.*?)</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def content_hash(text: str) -> str:
    """Stable 16-hex fingerprint, matching ``ingest_common.content_hash``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def slug(url: str) -> str:
    """Stable path-ish slug for the deterministic ``doc_id``."""
    tail = url.split("://", 1)[-1]
    return _SLUG_RE.sub("-", tail.lower()).strip("-")[-90:] or "page"


def page_to_text(body: str, *, is_html: bool) -> str:
    """Return clean text for a fetched page body, or ``""`` when it is not usable."""
    text = body or ""
    if is_html:
        text = _NOISE_BLOCK_RE.sub(" ", text)
        main = _MAIN_BLOCK_RE.search(text)
        if main:
            text = main.group(2)
        text = re.sub(r"<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", text, flags=re.IGNORECASE)
        text = _TAG_RE.sub(" ", text)
        text = html_module.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def doc_from_page(site: DocSite, url: str, body: str, *, is_html: bool) -> IngestDoc | None:
    """Normalize one fetched page into an IngestDoc, or ``None`` when it is too thin.

    The licence and the canonical family URL travel on the document's metadata so a
    point is self-describing after it leaves this process: a retriever that surfaces a
    GPL-3.0 Solidity page can say so, and a product-bound pass can filter on it.
    """
    text = page_to_text(body, is_html=is_html)
    if len(text) < MIN_CONTENT_CHARS:
        return None
    page_title = site.family
    head = text.splitlines()[0].strip() if text else ""
    if head and len(head) < 160:
        page_title = head
    content = (
        f"{site.family} documentation — {site.name}.\n"
        f"Licence: {site.licence}\n"
        f"Canonical source: {site.url}\n"
        f"Page: {url}\n\n"
        f"{text}"
    )
    return IngestDoc(
        content=content,
        doc_id=f"{site.name}:{slug(url)}:{content_hash(text[:4000])}",
        doc_type=site.doc_type,
        source=site.name,
        title=page_title,
        url=url,
        metadata={
            "collection": site.collection,
            "family": site.family,
            "licence": site.licence,
            "canonical_url": site.url,
            "source_url": url,
            "doc_type": site.doc_type,
            "ingest_group": "devdocs",
        },
    )


__all__ = [
    "MIN_CONTENT_CHARS",
    "content_hash",
    "doc_from_page",
    "page_to_text",
    "slug",
]
