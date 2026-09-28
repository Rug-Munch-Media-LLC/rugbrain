"""RugBrain — the ingest-vs-content decision, in one place.

This module answers one question: **is this payload field evidence of when the
corpus was last fed, or a fact about the world?**

Two clocks, routinely confused:

* a **content date** is a property of the world — "the exploit happened on the
  9th", ``metadata.published``, ``metadata.due_date``. Measured 2026-09-28:
  ``rag_documents`` and ``defi_exploits`` both carry content dates of
  2026-12-08, eleven days in the FUTURE, so a content-date check reads them as
  "fresh". A 2026-12-08 exploit has not happened yet.
* an **ingest time** is a fact about the corpus — stamped by the ingester when
  the point was written. It is the only thing that answers "is this feed dead".

:func:`classify_field` is the ONLY function that decides, so no caller can
quietly promote a content field into ingest evidence. :data:`INGEST_FIELDS` is
an allowlist: a field outside it is never read as ingest time, however
plausible it looks.

NO CONTENT DATE IS EVER PROMOTED, NOT EVEN FOR A DOCUMENT
--------------------------------------------------------
An earlier version of this module accepted ``ts``/``date`` as an ingest proxy
for a document-shaped corpus, on the reasoning that a news article's own clock
is a decent stand-in for fetch time. Measured against live Qdrant, that
reasoning is wrong in both directions and was removed:

* ``protocol_design`` — 58 architecture documents whose ``date`` is a
  *revision* date. Read as ingest time it reported the corpus 13d "aging",
  which said nothing about whether the feed ran.
* ``news_v2`` — 162,476 points carrying ``ts``. The first run classified it
  unmeasurable, the second MEASURED-and-fresh, purely on which points a 250
  sample happened to reach. A verdict that flips between runs on a stable
  collection is not a measurement.

Both are now unmeasurable, which is the honest answer. The cost is two more
gaps; the benefit is that no number in the report moves for a reason that is
not about the feed. A gap is a deliverable; a wrong measurement is worse.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

#: The ONLY fields that may be read as ingest time, most-trusted first.
#: Ordered deliberately: a collection stamping ``ingested_at`` is preferred
#: over one that only stamps ``detected_at``. Every entry names an event the
#: INGESTER performed on arrival, so its value is a fact about the corpus.
INGEST_FIELDS: Final[tuple[str, ...]] = (
    "metadata.ingested_at",
    "ingested_at",
    "indexed_at",
    "metadata.indexed_at",
    "ingest_ts",
    "ingest_timestamp",
    "detected_at",
    "metadata.detected_at",
    "first_seen",
    "metadata.first_seen",
    "seen_at",
    "loaded_at",
    "created_at",
    "metadata.created_at",
)

#: Content-side fields. Read only to name them in the report, never as ingest.
#: `ts`/`date` sit here deliberately: a document's own clock, which for a
#: news item or an architecture doc describes the DOCUMENT, not the feed.
CONTENT_FIELDS: Final[tuple[str, ...]] = (
    "ts",
    "metadata.ts",
    "date",
    "metadata.date",
    "metadata.published",
    "published",
    "published_at",
    "metadata.event_date",
    "event_date",
    "exploit_date",
    "metadata.year",
    "year",
)

#: Neither ingest nor content: an agent-inserted stamp recording when a
#: judgement was made, not when the corpus was fed. `due_date` is the measured
#: offender — it reads as a date and means nothing about ingestion.
AGENT_STAMPED: Final[tuple[str, ...]] = ("metadata.due_date", "due_date")


class FieldClass(StrEnum):
    """How a field may be used. Only ``INGEST`` is ever read as ingest time."""

    INGEST = "ingest"
    CONTENT = "content"
    AGENT = "agent"
    UNKNOWN = "unknown"


def classify_field(field: str, collection: str = "") -> FieldClass:
    """Decide ingest-vs-content for one field. The single decision point.

    ``collection`` is accepted so a caller cannot pass a field name without
    saying which corpus it is claiming about; the answer is deliberately the
    same for every corpus, because no corpus makes a content date into
    ingest evidence.
    """
    if field in AGENT_STAMPED:
        return FieldClass.AGENT
    if field in INGEST_FIELDS:
        return FieldClass.INGEST
    if field in CONTENT_FIELDS:
        return FieldClass.CONTENT
    return FieldClass.UNKNOWN


def is_ingest_field(field: str, collection: str = "") -> bool:
    """True when ``field`` may be read as ingest time for ``collection``."""
    return classify_field(field, collection) is FieldClass.INGEST
