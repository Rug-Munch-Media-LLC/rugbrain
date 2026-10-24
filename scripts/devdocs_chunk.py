"""Section-aware chunking for documentation sources; fixed windows for everything else.

WHY A DISPATCH AND NOT A NEW PIPELINE. ``ingest_all_sources`` owns chunk → embed →
Qdrant and that ownership does not move (``docs/ADDING-A-SOURCE.md``). The one thing that
does not generalise is the WINDOW: a doc page has real section boundaries and real code
blocks, and a fixed 2000-character cut through the middle of a signature or an API table
turns an authoritative page into two plausible-looking half-facts. ``split_doc_into_chunks``
already sections markdown/reST/AsciiDoc and keeps ``<pre>``-derived code as one block, so
the devdocs lane routes through it and every other source keeps the exact behaviour it had.

THE FALLBACK IS DELIBERATE. A malformed page must not abort a batch: if the sectioning
pass raises, the same text goes through the fixed windower the rest of the corpus uses, and
the run still stores the document. Losing section context is a quality regression; losing
the document is a data loss.

Selection is by ``metadata["ingest_group"]`` — the tag the devdocs fetcher puts on every
document it emits — not by ``doc_type``, because ``language_docs`` is also the doc_type of
a Solidity snippet source and those two want different treatment.
"""

from __future__ import annotations

import logging
from typing import Any

from ingest_common import chunk_text

logger = logging.getLogger(__name__)

#: Groups whose text is structured prose + code, and therefore worth sectioning.
SECTIONED_GROUPS = frozenset({"devdocs"})


def chunks_for_doc(content: str, metadata: dict[str, Any] | None = None) -> list[str]:
    """Chunk one document's content, section-aware when it is structured docs."""
    meta = metadata or {}
    if str(meta.get("ingest_group", "")) not in SECTIONED_GROUPS:
        return chunk_text(content)
    try:
        from rugbrain.app_chunking import split_doc_into_chunks

        parts = split_doc_into_chunks(content, "default", doc_meta=dict(meta))
        texts = [str(part["text"]) for part in parts if str(part.get("text", "")).strip()]
        return texts or chunk_text(content)
    except Exception as exc:  # one malformed page must not abort a 58-page batch
        logger.warning("devdocs_section_chunk_fallback err=%s", str(exc)[:120])
        return chunk_text(content)


__all__ = ["SECTIONED_GROUPS", "chunks_for_doc"]
