"""RAGAS evaluation metrics — precision, recall, hit rate, MRR, faithfulness."""
from __future__ import annotations

import re


def context_precision(retrieved_ids: list[str], relevant_ids: list[str], k: int | None = None) -> float:
    """
    Compute nDCG@k (Normalized Discounted Cumulative Gain).
    Measures ranking quality of retrieved documents.
    """
    if not relevant_ids or not retrieved_ids:
        return 0.0

    k = k or len(retrieved_ids)
    k = min(k, len(retrieved_ids))
    if k == 0:
        return 0.0

    # DCG
    dcg = 0.0
    for i in range(k):
        doc_id = retrieved_ids[i] if i < len(retrieved_ids) else ""
        rel = 1.0 if doc_id in relevant_ids else 0.0
        dcg += rel / math.log2(i + 2)  # i+2 because positions are 1-indexed

    # Ideal DCG
    ideal_rels = [1.0] * min(len(relevant_ids), k)
    ideal_rels += [0.0] * (k - len(ideal_rels))
    idcg = 0.0
    for i, rel in enumerate(ideal_rels):
        idcg += rel / math.log2(i + 2)

    if idcg == 0:
        return 0.0

    return dcg / idcg


def context_recall(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """
    Percentage of relevant documents found in retrieved results.
    """
    if not relevant_ids:
        return 1.0  # nothing to recall

    found = sum(1 for rid in relevant_ids if rid in set(retrieved_ids))
    return found / len(relevant_ids)


def hit_rate(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """
    Binary: 1.0 if at least one relevant doc was found, 0.0 otherwise.
    """
    if not relevant_ids:
        return 1.0
    return 1.0 if any(rid in set(retrieved_ids) for rid in relevant_ids) else 0.0


def mrr(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """
    Mean Reciprocal Rank: 1/rank of first relevant document.
    """
    if not relevant_ids:
        return 1.0
    for i, doc_id in enumerate(retrieved_ids):
        if doc_id in set(relevant_ids):
            return 1.0 / (i + 1)
    return 0.0


async def faithfulness(answer: str, context_chunks: list[str]) -> float:
    """
    Check if answer claims are supported by context.
    Uses NLI from hallucination_guard if available, otherwise keyword overlap.
    """
    if not answer or not context_chunks:
        return 0.0

    try:
        from app.hallucination_guard import HallucinationGuard  # external dependency

        guard = await HallucinationGuard.get_guard()
        combined_context = "\n\n".join(context_chunks[:5])
        result = await guard.check_answer(answer, combined_context)
        return result.confidence if result.is_faithful else 1.0 - result.confidence
    except Exception as e:
        logger.debug("NLI faithfulness failed, using keyword overlap: %s", e)
        return _keyword_faithfulness(answer, context_chunks)


def _keyword_faithfulness(answer: str, context_chunks: list[str]) -> float:
    """
    Simple keyword overlap faithfulness metric.
    Fraction of answer keywords found in context.
    """
    # Extract meaningful keywords from answer (skip stop words)
    stop_words = {
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

    answer_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", answer) if w.lower() not in stop_words}

    if not answer_words:
        return 0.5  # can't determine

    combined_context = " ".join(context_chunks).lower()
    supported = sum(1 for w in answer_words if w in combined_context)

    return supported / len(answer_words)


async def answer_relevancy(answer: str, query: str) -> float:
    """
    Cosine similarity between answer embedding and query embedding.
    Uses the local BGE embedder.
    """
    if not answer or not query:
        return 0.0

    try:
        from rmi_rag.crypto_embeddings import get_embedder

        embedder = await get_embedder()

        answer_vec = await embedder._semantic_embed_one(answer, "semantic")
        query_vec = await embedder._semantic_embed_one(query, "semantic")

        # Cosine similarity
        a = np.array(answer_vec)
        q = np.array(query_vec)
        sim = float(np.dot(a, q) / (np.linalg.norm(a) * np.linalg.norm(q) + 1e-8))

        # Normalize to [0, 1] (cosine sim is [-1, 1])
        return max(0.0, (sim + 1.0) / 2.0)
    except Exception as e:
        logger.debug("Answer relevancy embedding failed: %s", e)
        return _keyword_relevancy(answer, query)


def _keyword_relevancy(answer: str, query: str) -> float:
    """Fallback keyword-based relevancy."""
    query_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", query)}
    answer_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", answer)}
    if not query_words:
        return 0.0
    overlap = len(query_words & answer_words)
    return overlap / len(query_words)


# ======================================================================
# EVALUATION DATA MODELS
# ======================================================================


