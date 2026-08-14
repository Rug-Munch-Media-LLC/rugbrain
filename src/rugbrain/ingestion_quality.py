"""RugBrain — ingest-quality scoring for the continuous ingestion layer.

Scores every ingested document/chunk on five quality axes *before* it
reaches the vector store so low-value content can be filtered or
down-ranked — this is the "quality improves over time" half of the
continuous ingestion story:

  * ``length``           — enough substance to be retrievable (0-100)
  * ``structure``        — headings, code blocks, lists, paragraph breaks
  * ``source_authority`` — source credibility (reuses ``news_rating``'s
                           public reputation table; unknown sources score low)
  * ``uniqueness``       — dedup status (content fingerprint vs. seen set)
  * ``crypto_signals``   — verifiable crypto/security signals (addresses,
                           tx hashes, amounts, vulnerability terms)

The composite 0-100 ``QualityScore`` is tagged into document metadata
(``quality_score``, ``quality_tier``, ``quality_axes``,
``quality_signals``, ``is_duplicate``) so the retrieval layer can filter
``min_quality`` or down-rank by score.

Pure Python + pydantic, network-free, deterministic — safe to run inside
ingestion scripts and the API process. Works on the canonical
``ingest_common.CrawlDocument`` shape; raw text chunks are scored with
``score_chunk``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from rugbrain.ingest_common import CrawlDocument, content_hash
from rugbrain.news_rating import source_reputation_score

# ── Axis registry ───────────────────────────────────────────────────────

QUALITY_AXES: tuple[str, ...] = (
    "length",
    "structure",
    "source_authority",
    "uniqueness",
    "crypto_signals",
)

QUALITY_WEIGHTS: dict[str, float] = {
    "length": 0.20,
    "structure": 0.15,
    "source_authority": 0.30,
    "uniqueness": 0.20,
    "crypto_signals": 0.15,
}

DEFAULT_MIN_QUALITY = 40


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


# ── Length ──────────────────────────────────────────────────────────────


def length_score(text: str) -> int:
    """0-100: enough substance to be worth embedding.

    Tiny/empty payloads score near zero; a few hundred words score high.
    Extremely long walls of text are capped slightly below perfect so they
    do not dominate the ranking.
    """
    words = len((text or "").split())
    if words == 0:
        return 0
    if words < 10:
        return 15
    if words < 30:
        return 40
    if words < 80:
        return 65
    if words < 300:
        return 85
    return 95


# ── Structure ───────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s")
_CODE_FENCE_RE = re.compile(r"(?m)^\s*(?:```|~~~)")
_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def structure_score(text: str) -> int:
    """0-100: structural richness (headings, code, lists, paragraphs).

    Plain prose gets a small base; each structural marker adds a capped
    bonus so well-organized documents (post-mortems, guides, standards)
    score higher than raw text dumps.
    """
    stripped = (text or "").strip()
    if not stripped:
        return 0
    if len(stripped.split()) < 5:
        return 10
    score = 30  # base: readable prose exists
    score += min(len(_HEADING_RE.findall(stripped)) * 10, 30)
    score += min(len(_CODE_FENCE_RE.findall(stripped)) * 10, 20)
    score += min(len(_LIST_ITEM_RE.findall(stripped)) * 5, 15)
    score += min(len(_PARAGRAPH_RE.findall(stripped)) * 2, 10)
    return _clamp(score)


# ── Source authority ────────────────────────────────────────────────────


def source_authority_score(source: str, url: str = "") -> int:
    """0-100: credibility of the publishing source.

    Delegates to ``news_rating.source_reputation_score`` so the whole
    reputation table (wire services > crypto-native > anonymous blogs) is
    shared, not duplicated.
    """
    return source_reputation_score(source, url)


# ── Uniqueness / dedup ──────────────────────────────────────────────────


def uniqueness_score(is_duplicate: bool) -> int:
    """0-100: duplicate content scores near zero and can be filtered."""
    return 10 if is_duplicate else 100


# ── Crypto/security signals ─────────────────────────────────────────────

_EVM_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_TX_HASH_RE = re.compile(r"\b0x[a-fA-F0-9]{64}\b")
_SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_USD_AMOUNT_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?[kmb]?\b", re.IGNORECASE)
_TOKEN_AMOUNT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:ETH|BTC|SOL|USDT|USDC|USDE|DAI|BNB|ARB|OP|TON)\b", re.IGNORECASE
)
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?%")

VULNERABILITY_TERMS: tuple[str, ...] = (
    "exploit",
    "reentrancy",
    "rug",
    "honeypot",
    "phishing",
    "vulnerability",
    "cve-",
    "drained",
    "stolen",
    "hacked",
    "backdoor",
    "flash loan",
    "oracle manipulation",
    "malicious",
    "scam",
    "post-mortem",
    "postmortem",
    "proof of concept",
    "bypass",
    "overflow",
    "underflow",
)
_VULNERABILITY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in VULNERABILITY_TERMS) + r")\b",
    re.IGNORECASE,
)


def crypto_signals_score(text: str) -> int:
    """0-100: density of verifiable crypto/security artifacts.

    Counts EVM addresses, tx hashes, Solana addresses, USD/token amounts,
    percentages and vulnerability keywords per 100 words. Content with no
    signals at all gets a small base so crypto-agnostic reference material
    is still ingestible — just ranked below signal-rich incident reports.
    """
    words = len((text or "").split())
    if words < 5:
        return 0
    hits = (
        len(_EVM_ADDRESS_RE.findall(text))
        + len(_TX_HASH_RE.findall(text))
        + len(_SOLANA_ADDRESS_RE.findall(text))
        + len(_USD_AMOUNT_RE.findall(text))
        + len(_TOKEN_AMOUNT_RE.findall(text))
        + len(_PERCENT_RE.findall(text))
        + len(_VULNERABILITY_RE.findall(text))
    )
    if hits == 0:
        return 20
    return _clamp(hits / words * 100 * 35)


# ── Composite + model ───────────────────────────────────────────────────


class QualityScore(BaseModel):
    """Result of scoring one document/chunk on the five quality axes."""

    composite: int = 0
    tier: str = "poor"
    axes: dict[str, int] = Field(default_factory=dict)
    signals: dict[str, list[str]] = Field(default_factory=dict)
    is_duplicate: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Quality payload to merge into Qdrant/doc metadata."""
        return {
            "quality_score": self.composite,
            "quality_tier": self.tier,
            "quality_axes": dict(self.axes),
            "quality_signals": {k: list(v) for k, v in self.signals.items()},
            "is_duplicate": self.is_duplicate,
        }


def _compose(
    axes: dict[str, int], signals: dict[str, list[str]], is_duplicate: bool
) -> QualityScore:
    composite = round(sum(QUALITY_WEIGHTS[axis] * axes[axis] for axis in QUALITY_AXES))
    composite = _clamp(composite)
    if composite >= 80:
        tier = "excellent"
    elif composite >= 65:
        tier = "good"
    elif composite >= 50:
        tier = "fair"
    else:
        tier = "poor"
    return QualityScore(
        composite=composite,
        tier=tier,
        axes=axes,
        signals=signals,
        is_duplicate=is_duplicate,
    )


def _collect_signals(axes: dict[str, int], is_duplicate: bool) -> dict[str, list[str]]:
    signals: dict[str, list[str]] = {}
    if axes["length"] < 40:
        signals["length"] = ["too_short"]
    if axes["structure"] < 40:
        signals["structure"] = ["unstructured_text"]
    if axes["source_authority"] < 50:
        signals["source_authority"] = ["low_authority_source"]
    if is_duplicate:
        signals["uniqueness"] = ["duplicate_content"]
    if axes["crypto_signals"] < 40:
        signals["crypto_signals"] = ["few_crypto_signals"]
    return signals


def score_chunk(
    text: str,
    *,
    source: str = "",
    url: str = "",
    is_duplicate: bool = False,
) -> QualityScore:
    """Score one raw text chunk on all five quality axes.

    ``source``/``url`` feed the source-authority axis; ``is_duplicate``
    (from vector or fingerprint dedup) feeds the uniqueness axis.
    """
    axes = {
        "length": length_score(text),
        "structure": structure_score(text),
        "source_authority": source_authority_score(source, url),
        "uniqueness": uniqueness_score(is_duplicate),
        "crypto_signals": crypto_signals_score(text),
    }
    return _compose(axes, _collect_signals(axes, is_duplicate), is_duplicate)


def score_document(doc: CrawlDocument, *, is_duplicate: bool = False) -> QualityScore:
    """Score a full ``CrawlDocument`` (uses the whole ``content`` field)."""
    return score_chunk(doc.content, source=doc.source, url=doc.url, is_duplicate=is_duplicate)


# ── Batch tagging + dedup + filtering ───────────────────────────────────


@dataclass
class TaggedBatch:
    """Result of tagging a batch of docs with quality + dedup flags."""

    docs: list[CrawlDocument]
    seen_fingerprints: set[str]
    duplicate_count: int


def tag_documents(
    docs: Sequence[CrawlDocument],
    *,
    seen_fingerprints: set[str] | None = None,
) -> TaggedBatch:
    """Score every doc, tag metadata, and dedup by content fingerprint.

    Documents whose content fingerprint was already seen (in *this* batch
    or a caller-provided set, e.g. from a previous source in the same run)
    are flagged ``is_duplicate`` and their uniqueness axis is zeroed. The
    updated fingerprint set is returned so callers can thread it across
    sources for cross-source dedup.

    Mutates ``doc.metadata`` in place with the quality payload.
    """
    seen = set(seen_fingerprints or ())
    duplicate_count = 0
    tagged: list[CrawlDocument] = []
    for doc in docs:
        fingerprint = content_hash(doc.content or "")
        is_duplicate = fingerprint in seen
        if is_duplicate:
            duplicate_count += 1
        else:
            seen.add(fingerprint)
        score = score_document(doc, is_duplicate=is_duplicate)
        doc.metadata.update(score.to_metadata())
        tagged.append(doc)
    return TaggedBatch(docs=tagged, seen_fingerprints=seen, duplicate_count=duplicate_count)


def filter_low_quality(
    docs: Sequence[CrawlDocument],
    min_score: int = DEFAULT_MIN_QUALITY,
) -> tuple[list[CrawlDocument], list[CrawlDocument]]:
    """Split docs into ``(kept, dropped)`` by tagged ``quality_score``.

    Docs that were never tagged (no ``quality_score`` metadata) are kept —
    the filter must never silently drop untagged content.
    """
    kept: list[CrawlDocument] = []
    dropped: list[CrawlDocument] = []
    for doc in docs:
        score = int(doc.metadata.get("quality_score", min_score) or 0)
        if score >= min_score:
            kept.append(doc)
        else:
            dropped.append(doc)
    return kept, dropped


def rank_by_quality(docs: Sequence[CrawlDocument], *, reverse: bool = True) -> list[CrawlDocument]:
    """Stable-sort docs by tagged ``quality_score`` (highest first by default)."""
    return sorted(docs, key=lambda d: int(d.metadata.get("quality_score", 0) or 0), reverse=reverse)


__all__ = [
    "DEFAULT_MIN_QUALITY",
    "QUALITY_AXES",
    "QUALITY_WEIGHTS",
    "QualityScore",
    "TaggedBatch",
    "crypto_signals_score",
    "filter_low_quality",
    "length_score",
    "rank_by_quality",
    "score_chunk",
    "score_document",
    "source_authority_score",
    "structure_score",
    "tag_documents",
    "uniqueness_score",
]
