"""Declared corpora, group: knowledge.

RAG and research corpora: documents, education, research, standards, regulation, the
skill corpus, and the two text feeds that are documentation rather than code.
Runtime state and probes are declared separately in ``entries_runtime``.

Every row is transcribed from ``docs/COVERAGE-AUDIT-2026-09-28.md`` §1 (96 collections,
6,314,262 points, dims {1024, 2560, 4096}). LANGUAGE and DOMAIN are what this file adds:
the audit measured the collections but never said which axis a repo belonged to, which is
how the Solidity-repos-are-the-code-corpus belief survived. See ``corpus/entries.py``.

See ``corpus/schema.py`` for what each field means and ``corpus/gate.py`` for the refusal.
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.entries_base import DAILY, MONTHLY, WEEKLY, D, L, _d
from rugbrain.corpus.schema import (
    LICENCE_PERMISSIVE,
    LICENCE_PUBLIC_DOMAIN,
    LICENCE_UNLICENSED,
    CorpusDeclaration,
)

_KNOWLEDGE: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "rag-documents",
        "rag_documents",
        cid="rag_documents",
        language=L.DOCS,
        domain=D.KNOWLEDGE,
        url="internal://rag/documents",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="530,323 pts. Named vector. Carries content dates 11 days in the "
        "future (audit §5) — a content date is not an ingest date. Base 2560.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "education",
        "education",
        cid="education",
        language=L.DOCS,
        domain=D.KNOWLEDGE,
        url="internal://rag/education",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="4,668 pts.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "research-v2",
        "research_v2",
        cid="research_v2",
        language=L.DOCS,
        domain=D.KNOWLEDGE,
        url="internal://rag/research",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="158 pts against a 2.13M code corpus = 0.0074%. The largest structural "
        "imbalance in the fleet (audit §3.1).",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "standards-ethereum-eips",
        "standards",
        cid="standards",
        language=L.MARKDOWN,
        domain=D.KNOWLEDGE,
        url="https://github.com/ethereum/EIPs",
        licence=LICENCE_PUBLIC_DOMAIN,
        cadence=WEEKLY,
        notes="0 points. The 957 EIPs are the declared target and the cheapest "
        "high-value fill (candidate rank 1, CC0).",
        observed=4096,
        retired=()
    ),
    _d(
        "regulation-enforcement",
        "regulation_enforcement",
        cid="regulation_enforcement",
        language=L.DOCS,
        domain=D.KNOWLEDGE,
        url="internal://rag/regulation-enforcement",
        licence=LICENCE_PERMISSIVE,
        cadence=MONTHLY,
        notes="5,081 pts but content age 1320d — HISTORICAL ONLY.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "master-skills",
        "master_skills",
        cid="master_skills",
        language=L.MARKDOWN,
        domain=D.KNOWLEDGE,
        url="internal://skills/master-corpus",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="4,495 pts at 2560. Twin exists but NOT owned (lane_table).",
        observed=2560,
        retired=()
    ),
    _d(
        "crime-reports",
        "crime_reports",
        cid="crime_reports",
        language=L.DOCS,
        domain=D.KNOWLEDGE,
        url="internal://rag/crime-reports",
        licence=LICENCE_PERMISSIVE,
        cadence=MONTHLY,
        notes="0 points, never fed.",
        observed=4096,
        retired=()
    ),
    _d(
        "youtube-videos",
        "youtube_videos",
        cid="youtube_videos",
        language=L.DOCS,
        domain=D.KNOWLEDGE,
        url="internal://pipe/youtube",
        licence=LICENCE_UNLICENSED,
        cadence=WEEKLY,
        notes="Pinned 1024. 0 points. Unlicensed: internal-only, not product-cleared.",
        observed=1024,
        retired=()
    ),
)

__all__ = [
    "_KNOWLEDGE",
]
