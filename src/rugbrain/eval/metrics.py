"""
Lightweight RAG-quality metrics — faithfulness, answer relevance,
context precision/recall — WITHOUT the heavy ``ragas`` dependency.

These implement the core ragas formulas as lexical approximations so they
run with zero model/network deps (pure stdlib). They are intentionally
cheaper and noisier than ragas' LLM/NLI-based scoring:

* ``faithfulness`` — fraction of answer claims (sentences) whose non-stopword
  tokens are supported by the retrieved context.
* ``answer_relevance`` — token overlap between the answer and the query,
  optionally upgraded to real embedding cosine similarity by passing an
  ``embed`` callable (e.g. ``get_embedder``).
* ``context_precision`` — ragas-style mean precision@i over the ranks where a
  relevant doc appears (a stricter measure than NDCG).
* ``context_recall`` — fraction of relevant ids retrieved (alias of
  ``recall_at_k`` with no cutoff).

Optional ``ragas`` integration: if the real ``ragas`` package is installed it
is imported lazily (never at module import time) via ``ragas_available()`` so
a team can cross-check the lightweight numbers against the full pipeline.

[REVIEW] These are lexical proxies, not the ragas LLM judgments. Use them for
regression tracking of retrieval/rerank changes (fast, deterministic), and
reserve ragas for periodic human-verified audits.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence

from rugbrain.eval.ndcg import recall_at_k

# ── tokenization helpers ─────────────────────────────────────────────

_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "if",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "we",
        "you",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "their",
        "our",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "than",
        "too",
        "very",
    }
)

_TOKEN_RE = re.compile(r"\b[\w$][\w$]{1,}\b", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """Lowercased non-stopword tokens from *text* (3+ chars)."""
    return {w.lower() for w in _TOKEN_RE.findall(text) if w.lower() not in _STOP_WORDS}


def _split_claims(answer: str) -> list[str]:
    """Split an answer into claim-sized units (sentences)."""
    parts = re.split(r"(?<=[.!?])\s+", answer.strip())
    return [p for p in (s.strip() for s in parts) if p]


# ── faithfulness ─────────────────────────────────────────────────────

# A claim counts as supported when this fraction of its non-stopword tokens
# appears in the concatenated context.
_SUPPORT_THRESHOLD = 0.5


def faithfulness(answer: str, context_chunks: Sequence[str]) -> float:
    """
    Fraction of answer claims supported by the retrieved context.

    A claim is supported when at least ``_SUPPORT_THRESHOLD`` (default 0.5) of
    its non-stopword tokens appear in the concatenated context. Returns 0.0
    for an empty answer/context or when no claims can be parsed.

    This is a lexical proxy for ragas' NLI-based faithfulness — cheap and
    deterministic, but it will under-count paraphrase support.
    """
    if not answer or not context_chunks:
        return 0.0

    claims = _split_claims(answer)
    if not claims:
        return 0.0

    combined = " ".join(context_chunks).lower()
    supported = 0
    for claim in claims:
        tokens = _tokens(claim)
        if not tokens:
            continue
        hits = sum(1 for tok in tokens if tok in combined)
        if hits / len(tokens) >= _SUPPORT_THRESHOLD:
            supported += 1
    if supported == 0:
        return 0.0
    return supported / len(claims)


# ── answer relevance ─────────────────────────────────────────────────


def answer_relevance(
    answer: str,
    query: str,
    embed: Callable[[str], list[float]] | None = None,
) -> float:
    """
    Relevance of *answer* to *query*, normalized to [0.0, 1.0].

    Default (no ``embed``): fraction of non-stopword query tokens that appear
    in the answer (lexical recall of query terms).

    With ``embed``: cosine similarity between ``embed(query)`` and
    ``embed(answer)``, mapped from [-1, 1] to [0, 1]. The embed callable is
    called synchronously; wrap an async embedder (e.g. the  Qwen3 embedder) with
    ``asyncio.run`` or a thread executor if needed.

    Returns 0.0 for an empty answer or query.
    """
    if not answer or not query:
        return 0.0

    if embed is not None:
        q_vec = embed(query)
        a_vec = embed(answer)
        sim = _cosine(q_vec, a_vec)
        return max(0.0, (sim + 1.0) / 2.0)

    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0
    a_tokens = _tokens(answer)
    if not a_tokens:
        return 0.0
    overlap = len(q_tokens & a_tokens)
    return overlap / len(q_tokens)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity with a zero-vector guard (returns 0.0 on divide-by-zero)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── context precision / recall ───────────────────────────────────────


def context_precision(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
    k: int | None = None,
) -> float:
    """
    Ragas-style context precision@k.

    For each rank (1-indexed) at which a relevant id appears, compute
    precision@rank (fraction of retrieved ids up to that rank that are
    relevant), then average over all such ranks. This is stricter than NDCG:
    relevant docs buried behind many irrelevant ones score lower.

    Returns 0.0 when no relevant id is retrieved, or the relevant set is empty.
    """
    rel_set = set(relevant_ids)
    if not rel_set or not retrieved_ids:
        return 0.0
    k = min(k if k is not None else len(retrieved_ids), len(retrieved_ids))
    if k <= 0:
        return 0.0

    relevant_ranks = [i for i in range(k) if retrieved_ids[i] in rel_set]
    if not relevant_ranks:
        return 0.0

    total = 0.0
    for rank in relevant_ranks:
        window = retrieved_ids[: rank + 1]
        precision = sum(1 for rid in window if rid in rel_set) / len(window)
        total += precision
    return total / len(relevant_ranks)


def context_recall(
    retrieved_ids: Sequence[str],
    relevant_ids: Iterable[str],
) -> float:
    """Fraction of relevant ids retrieved (alias of ``recall_at_k``, no cutoff)."""
    return recall_at_k(retrieved_ids, relevant_ids, k=None)


# ── optional ragas integration (lazy) ────────────────────────────────


def ragas_available() -> bool:
    """
    True when the real ``ragas`` package is importable (never imported here).

    Lets callers opt into ragas cross-validation without making it a hard dep.
    """
    try:
        import importlib.util

        return importlib.util.find_spec("ragas") is not None
    except Exception:  # pragma: no cover - defensive
        return False


def ragas_metric_names() -> list[str]:
    """
    Names of the ragas metrics this module mirrors, if ragas is installed.

    Used for reporting parity between the lightweight and full metrics.
    The ragas module is loaded dynamically (never statically imported) so the
    module stays importable — and mypy-clean in both strict and
    ``--ignore-missing-imports`` modes — whether or not ragas is installed.
    """
    if not ragas_available():
        return []
    try:
        import importlib

        metrics_mod = importlib.import_module("ragas.metrics")
        names = [
            str(getattr(getattr(metrics_mod, metric), "name", metric))
            for metric in ("faithfulness", "answer_relevancy", "context_precision")
        ]
        return names
    except Exception:  # pragma: no cover - ragas API drift
        return []


__all__: list[str] = [
    "answer_relevance",
    "context_precision",
    "context_recall",
    "faithfulness",
    "ragas_available",
    "ragas_metric_names",
]
