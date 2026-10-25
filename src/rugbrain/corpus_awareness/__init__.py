"""Corpus awareness: what our own corpus holds, and cited answers from it.

Two seams keep this testable and honest: `CorpusProbe` (live Qdrant) and
`Searcher` (the existing /api/v1/search path). No static manifests.
"""

from rugbrain.corpus_awareness.ask import ask_corpus
from rugbrain.corpus_awareness.map_view import build_corpus_map, render_brief
from rugbrain.corpus_awareness.probe import CorpusProbe, HttpQdrantProbe, QdrantUnreachable
from rugbrain.corpus_awareness.routes import exposed_by, route_question
from rugbrain.corpus_awareness.searcher import HttpSearch, Searcher

__all__ = [
    "CorpusProbe",
    "HttpQdrantProbe",
    "HttpSearch",
    "QdrantUnreachable",
    "Searcher",
    "ask_corpus",
    "build_corpus_map",
    "exposed_by",
    "render_brief",
    "route_question",
]

#: Marker used instead of `[]` when a live collection holds no points.
CORPUS_EMPTY = {"empty": True, "samples": []}
