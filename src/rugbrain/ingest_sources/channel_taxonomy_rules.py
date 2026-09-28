"""Ordered, word-bounded keyword rules for the Telegram channel taxonomy.

Split from :mod:`rugbrain.ingest_sources.channel_taxonomy` on purpose: a data
table is not logic, and keeping it in its own modules keeps every product module
inside the 200-line firm limit. Matching is word-bounded (see
:mod:`rugbrain.ingest_sources.channel_naming`) and ordered most-specific first
— first match wins.

The table is held in two halves so neither file can grow past the limit:
crime lanes in :mod:`channel_taxonomy_crime`, informational lanes in
:mod:`channel_taxonomy_intel`. This module is the public surface: it re-exports
the needles in evaluation order and nothing else imports the halves directly.

Rule names are more expressive than the stored payload vocabulary
(``alpha_caller``/``research``) and are aliased by the taxonomy module onto the
values ``news_filter`` already knows, so a new rule name can never demote a doc
to ``noise``.

Order: security → scam_caller → alpha_caller → research → data → market → news.
Security is checked first because "exploit" is a scam-caller's best content and
must not be stolen by the fraud lane; the informational lanes follow risk lanes
so "Scam Report" stays fraud, not research.
"""

from rugbrain.ingest_sources.channel_taxonomy_crime import (
    ALPHA_CALLER_NEEDLES,
    SCAM_CALLER_NEEDLES,
    SECURITY_NEEDLES,
)
from rugbrain.ingest_sources.channel_taxonomy_intel import (
    DATA_NEEDLES,
    MARKET_NEEDLES,
    NEWS_NEEDLES,
    RESEARCH_NEEDLES,
)

__all__ = [
    "ALPHA_CALLER_NEEDLES",
    "DATA_NEEDLES",
    "MARKET_NEEDLES",
    "NEWS_NEEDLES",
    "RESEARCH_NEEDLES",
    "SCAM_CALLER_NEEDLES",
    "SECURITY_NEEDLES",
]
