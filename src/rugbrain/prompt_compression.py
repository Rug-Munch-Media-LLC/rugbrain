"""
Prompt compression + "lost in the middle" mitigation for RugBrain RAG.

Self-contained, additive library that squeezes long retrieved contexts into
the small context windows of quantized / low-parameter models. Pure library:
the search/ and ingest_sources/ lanes are untouched; callers opt in.

* ``compress_context`` — LLMLingua-style compression. Uses the ``llmlingua``
  package when installed (lazy import; model loads on first use, stays
  resident). Falls back to a lightweight extractive compressor that keeps
  high-signal sentences (code blocks, addresses, numbers, vulnerability
  terms, section headers) and drops filler, keeping code and addresses
  intact.
* ``layout_context`` / ``pack_context`` — "lost in the middle" mitigation:
  highest-relevance chunks FIRST (top of window), lower-relevance in the
  middle, user query LAST. Works with rerank scores and retrieval order.

Intended call order: retrieve -> rerank -> compress -> invert -> prompt::

    results = await search_similar(...)                      # 1. retrieve
    results = await rerank(query, results)                   # 2. rerank
    results = compress_context(results, 2048, query=query)   # 3. compress
    prompt  = pack_context(results, query)                   # 4. invert

Environment: ``LLMLINGUA_MODEL`` overrides the llmlingua HF model id
(default: microsoft/llmlingua-2-xlm-roberta-large-meeting-bank).
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── llmlingua availability (probed once; model loads lazily) ──────────────

LLMLINGUA_MODEL = os.getenv(
    "LLMLINGUA_MODEL", "microsoft/llmlingua-2-xlm-roberta-large-meeting-bank"
)
LLMLINGUA_AVAILABLE = importlib.util.find_spec("llmlingua") is not None

_LLMLINGUA_PROBED = False
_LLMLINGUA_COMPRESSOR: Any | None = None

# ── token estimation (matches app_chunking's ~4 chars/token) ──────────────

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (``~4 chars/token``), no tokenizer dependency."""
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


# ── high-signal detection ─────────────────────────────────────────────────

_FENCED_CODE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# EVM-ish hex (covers 0x...40 contracts), Solana base58, Bitcoin.
_ADDRESS_RE = re.compile(
    r"\b0x[a-fA-F0-9]{6,64}\b"
    r"|[1-9A-HJ-NP-Za-km-z]{32,44}"
    r"|\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b"
)
_PLACEHOLDER_RE = re.compile(r"\x00([AC])(\d+)\x00")

_HEADER_RE = re.compile(r"^#{1,6}\s")
_HAS_NUMBER_RE = re.compile(r"\d")
_CODE_KEYWORD_RE = re.compile(
    r"\b(contract|function|interface|library|struct|enum|impl|trait|"
    r"pub fn|def |class |constructor|modifier|mapping|address payable|"
    r"uint256|require|assert|mut |unsafe|async fn|return|import |from |"
    r"pragma|solc)\b"
)
_VULN_TERMS = (
    "reentrancy",
    "rug pull",
    "rugpull",
    "honeypot",
    "drain",
    "exploit",
    "vulnerability",
    "phishing",
    "scam",
    "malicious",
    "attacker",
    "stolen",
    "hack",
    "breach",
    "unauthorized",
    "backdoor",
    "arbitrage",
    "slippage",
    "oracle manipulation",
    "flash loan",
    "approve",
    "allowance",
    "private key",
    "seed phrase",
    "liquidity removed",
    "lp removed",
    "kill switch",
    "upgradeable",
)
_FILLER_START_RE = re.compile(
    r"^(however|moreover|furthermore|additionally|in addition|"
    r"it is important|please note|note that|as you can see|let.s|"
    r"in conclusion|overall|in summary|for example|that being said|"
    r"having said that|it should be noted|we can see|essentially|"
    r"basically|interestingly|importantly|on the other hand|"
    r"in other words|as mentioned|as noted)\b",
    re.IGNORECASE,
)
_BOILERPLATE_RE = re.compile(
    r"\b(stay tuned|follow us|join our telegram|follow @|disclaimer|"
    r"not financial advice|do your own research|dyor|subscribe to)\b",
    re.IGNORECASE,
)

_CODE_BLOCK_SCORE = 12.0


@dataclass(frozen=True)
class _PromptUnit:
    """A sentence or atomic code block with a signal score."""

    text: str
    score: float
    order: int
    is_code: bool = False


def _protect_spans(
    text: str, *, protect_addresses: bool = True
) -> tuple[str, list[str], list[str]]:
    """Replace addresses / inline code with placeholders; return ``(text, addresses, code_spans)``."""
    addresses: list[str] = []
    code_spans: list[str] = []

    if protect_addresses:

        def _addr_repl(m: re.Match[str]) -> str:
            addresses.append(m.group(0))
            return f"\x00A{len(addresses) - 1}\x00"

        text = _ADDRESS_RE.sub(_addr_repl, text)

    def _code_repl(m: re.Match[str]) -> str:
        code_spans.append(m.group(0))
        return f"\x00C{len(code_spans) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_code_repl, text)
    return text, addresses, code_spans


def _unprotect(text: str, addresses: list[str], code_spans: list[str]) -> str:
    """Restore addresses / code spans from placeholders in *text*."""

    def _repl(m: re.Match[str]) -> str:
        pool = addresses if m.group(1) == "A" else code_spans
        idx = int(m.group(2))
        return pool[idx] if 0 <= idx < len(pool) else ""

    return _PLACEHOLDER_RE.sub(_repl, text)


def _split_sentences(text: str) -> list[str]:
    """Split into line/sentence fragments without breaking protected spans."""
    parts: list[str] = []
    for line in re.split(r"\r?\n+", text):
        line = line.strip()
        if not line:
            continue
        # Header / label lines ("## Risks", "Summary:") are atomic units.
        if _HEADER_RE.match(line) or line.endswith(":"):
            parts.append(line)
            continue
        for frag in re.split(r"(?<=[.!?])\s+", line):
            if frag.strip():
                parts.append(frag.strip())
    return parts


def _score_unit(text: str, *, has_addr: bool, has_code_span: bool) -> float:
    """Signal score: higher = kept first when compressing."""
    low = text.lower()
    score = 1.0
    if has_addr:
        score += 3.0
    if has_code_span:
        score += 1.5
    if _HAS_NUMBER_RE.search(text):
        score += 1.5
    if _HEADER_RE.search(low) or text.rstrip().endswith(":"):
        score += 2.0
    if _CODE_KEYWORD_RE.search(low):
        score += 1.5
    if any(term in low for term in _VULN_TERMS):
        score += 2.0
    if _FILLER_START_RE.match(low):
        score -= 2.0
    if _BOILERPLATE_RE.search(low):
        score -= 2.0
    return score


def _extract_units(
    text: str,
    *,
    keep_code_blocks: bool = True,
    keep_addresses: bool = True,
) -> list[_PromptUnit]:
    """Split *text* into scored units (sentences + atomic code blocks)."""
    units: list[_PromptUnit] = []
    rest = text

    if keep_code_blocks:
        # Fenced code blocks come out first and stay atomic (highest priority).

        def _code_cb(m: re.Match[str]) -> str:
            units.append(
                _PromptUnit(
                    text=m.group(0),
                    score=_CODE_BLOCK_SCORE,
                    order=len(units),
                    is_code=True,
                )
            )
            return ""

        rest = _FENCED_CODE_RE.sub(_code_cb, rest)

    protected, addresses, code_spans = _protect_spans(rest, protect_addresses=keep_addresses)
    for sentence in _split_sentences(protected):
        real = _unprotect(sentence, addresses, code_spans)
        if not real.strip():
            continue
        units.append(
            _PromptUnit(
                text=real,
                score=_score_unit(
                    real,
                    has_addr="\x00A" in sentence,
                    has_code_span="\x00C" in sentence,
                ),
                order=len(units),
            )
        )
    return units


def _slice_sentence(
    sentence: str,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> str:
    """Slice an oversized sentence around its high-signal tokens (address intact)."""
    limit = max(4, budget_tokens * _CHARS_PER_TOKEN)
    if count_tokens(sentence) <= budget_tokens:
        return sentence

    matches = list(_ADDRESS_RE.finditer(sentence))
    anchor = matches[0].start() if matches else None
    if anchor is None:
        number_m = _HAS_NUMBER_RE.search(sentence)
        anchor = number_m.start() if number_m else None

    if anchor is not None:
        half = limit // 2
        start = max(0, anchor - half)
        end = min(len(sentence), anchor + half)
        # Expand so every address touching the window survives whole.
        for m in matches:
            if m.start() < end and m.end() > start:
                start = min(start, m.start())
                end = max(end, m.end())
        if start > 0:
            space = sentence.rfind(" ", 0, start)
            if space != -1:
                start = space + 1
        if end < len(sentence):
            space = sentence.find(" ", end)
            if space != -1:
                end = space
        return sentence[start:end]

    head = sentence[:limit]
    return head.rsplit(" ", 1)[0] if " " in head else head


def _truncate_unit(
    unit: _PromptUnit,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> _PromptUnit:
    """Truncate an over-budget unit at a safe boundary (line / sentence)."""
    if unit.is_code:
        keep: list[str] = []
        used = 0
        for line in unit.text.splitlines():
            toks = count_tokens(line)
            if keep and used + toks > budget_tokens:
                break
            keep.append(line)
            used += toks
        # A single oversized line is kept whole — splitting code mid-line would
        # corrupt it; code integrity matters more than the token budget.
        return _PromptUnit("\n".join(keep) if keep else unit.text, unit.score, unit.order, True)

    keep = []
    used = 0
    for frag in _split_sentences(unit.text):
        toks = count_tokens(frag)
        if not keep and toks > budget_tokens:
            # Oversized single sentence: slice around its high-signal tokens.
            return _PromptUnit(
                _slice_sentence(frag, budget_tokens, count_tokens),
                unit.score,
                unit.order,
            )
        if keep and used + toks > budget_tokens:
            break
        keep.append(frag)
        used += toks
    return _PromptUnit(" ".join(keep), unit.score, unit.order)


def _select_units(
    units: list[_PromptUnit],
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> list[_PromptUnit]:
    """Pick the best units fitting the budget; the top unit always survives.

    The single best unit is always included (truncated when oversized) so
    compression never keeps filler over high-signal content; the remaining
    budget is filled with the next-best fitting units. Result keeps original
    document order.
    """
    if not units:
        return []

    ordered = sorted(units, key=lambda u: (-u.score, u.order))
    chosen: list[_PromptUnit] = []
    best = ordered[0]
    best_toks = count_tokens(best.text)
    if best_toks <= budget_tokens:
        chosen.append(best)
        remaining = budget_tokens - best_toks
    else:
        chosen.append(_truncate_unit(best, budget_tokens, count_tokens))
        remaining = budget_tokens - count_tokens(chosen[0].text)

    for unit in ordered[1:]:
        toks = count_tokens(unit.text)
        if toks <= remaining:
            chosen.append(unit)
            remaining -= toks

    chosen.sort(key=lambda u: u.order)
    return chosen


def _truncate_text_head(
    text: str,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> str:
    """Last-resort fallback: keep the leading sentences up to the budget."""
    keep: list[str] = []
    used = 0
    for frag in _split_sentences(text):
        toks = count_tokens(frag)
        if keep and used + toks > budget_tokens:
            break
        keep.append(frag)
        used += toks
    return " ".join(keep) if keep else text[: max(4, budget_tokens * _CHARS_PER_TOKEN)]


def _compress_text(
    text: str,
    budget_tokens: int,
    *,
    count_tokens: Callable[[str], int],
    keep_code_blocks: bool = True,
    keep_addresses: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Extractive compression of a single text; returns ``(text, stats)``."""
    text = (text or "").replace("\x00", "")
    stats: dict[str, Any] = {"kept_code_blocks": 0, "kept_addresses": 0}
    if budget_tokens <= 0 or count_tokens(text) <= budget_tokens:
        return text, stats

    units = _extract_units(
        text,
        keep_code_blocks=keep_code_blocks,
        keep_addresses=keep_addresses,
    )
    selected = _select_units(units, budget_tokens, count_tokens)
    if not selected:
        return _truncate_text_head(text, budget_tokens, count_tokens), stats

    out = "\n\n".join(u.text for u in selected)
    stats["kept_code_blocks"] = sum(1 for u in selected if u.is_code)
    stats["kept_addresses"] = len(_ADDRESS_RE.findall(out))
    return out, stats


def _get_llmlingua_compressor(model_name: str | None) -> Any | None:
    """Lazily create the LLMLingua ``PromptCompressor`` singleton (or ``None``)."""
    global _LLMLINGUA_PROBED, _LLMLINGUA_COMPRESSOR
    if not LLMLINGUA_AVAILABLE:
        return None
    if _LLMLINGUA_PROBED:
        return _LLMLINGUA_COMPRESSOR
    _LLMLINGUA_PROBED = True
    try:
        from llmlingua import (
            PromptCompressor,
        )  # optional dependency, guarded by LLMLINGUA_AVAILABLE

        _LLMLINGUA_COMPRESSOR = PromptCompressor(model_name=model_name or LLMLINGUA_MODEL)
        logger.info(
            "prompt_compression: llmlingua compressor loaded (%s)",
            model_name or LLMLINGUA_MODEL,
        )
    except Exception as exc:
        logger.warning("prompt_compression: llmlingua load failed (%s) — extractive fallback", exc)
        _LLMLINGUA_COMPRESSOR = None
    return _LLMLINGUA_COMPRESSOR


def _llmlingua_compress(
    text: str,
    budget_tokens: int,
    query: str | None,
    model_name: str | None,
) -> str:
    """Compress *text* with llmlingua's sync ``compress_prompt`` API."""
    compressor = _get_llmlingua_compressor(model_name)
    if compressor is None:
        raise RuntimeError("llmlingua unavailable")

    params = inspect.signature(compressor.compress_prompt).parameters
    kwargs: dict[str, Any] = {"context": text, "question": query or ""}
    if "target_token" in params:
        result = compressor.compress_prompt(**kwargs, target_token=max(1, budget_tokens))
    else:  # older llmlingua: rate-based compression
        rate = min(1.0, budget_tokens / max(1, _estimate_tokens(text)))
        result = compressor.compress_prompt(**kwargs, rate=max(0.05, rate))

    if isinstance(result, dict):
        out = result.get("compressed_prompt")
        if isinstance(out, str) and out:
            return out
    return text


def _distribute_budget(
    token_counts: list[int],
    target_total: int,
    floor: int,
) -> list[int]:
    """Allocate per-chunk budgets proportional to original size, fitting the total.

    Every chunk keeps at least ``floor`` tokens; budgets are clamped to the
    chunk's original size and scaled down until the sum fits *target_total*.
    """
    n = len(token_counts)
    total = sum(token_counts)
    if n == 0 or total <= 0:
        return []

    scale = 1.0
    while True:
        budgets = [max(floor, min(t, int(t * target_total / total * scale))) for t in token_counts]
        if sum(budgets) <= target_total or scale < 0.05:
            return budgets
        scale *= 0.9


def _get_text(chunk: dict[str, Any]) -> str | None:
    """Chunk text under ``content`` or ``text`` (matching search/rerank lanes)."""
    for key in ("content", "text"):
        val = chunk.get(key)
        if isinstance(val, str):
            return val
    return None


def _set_text(chunk: dict[str, Any], value: str) -> None:
    """Write compressed text back to the field the chunk originally used."""
    if "content" in chunk:
        chunk["content"] = value
    elif "text" in chunk:
        chunk["text"] = value
    else:
        chunk["content"] = value


def _annotate(
    entry: dict[str, Any],
    *,
    method: str,
    before: int,
    after: int,
    stats: dict[str, Any] | None = None,
) -> None:
    """Tag a result chunk with compression metadata (``compression_*`` keys)."""
    stats = stats or {}
    tags: list[str] = []
    if stats.get("kept_code_blocks"):
        tags.append("code")
    if stats.get("kept_addresses"):
        tags.append("address")
    entry["compressed"] = method != "unchanged"
    entry["compression_method"] = method
    entry["tokens_before"] = before
    entry["tokens_after"] = after
    entry["compression_kept_code_blocks"] = stats.get("kept_code_blocks", 0)
    entry["compression_kept_addresses"] = stats.get("kept_addresses", 0)
    entry["compression_tags"] = tags


def compress_context(
    chunks: Sequence[dict[str, Any]],
    target_tokens: int,
    *,
    query: str | None = None,
    count_tokens: Callable[[str], int] | None = None,
    use_llmlingua: bool = True,
    llmlingua_model: str | None = None,
    min_keep_tokens: int = 16,
    keep_code_blocks: bool = True,
    keep_addresses: bool = True,
) -> list[dict[str, Any]]:
    """Compress retrieved *chunks* to roughly *target_tokens* total.

    Parameters
    ----------
    chunks:
        Chunk dicts from retrieval/rerank (``content`` or ``text``; score
        keys are preserved untouched).
    target_tokens:
        Approximate total token budget for the compressed context.
    query:
        Optional query forwarded to llmlingua's ``question``.
    count_tokens:
        Optional token counter; defaults to ``~4 chars/token`` heuristic.
    use_llmlingua:
        Prefer LLMLingua when installed (default True); extractive otherwise.
    llmlingua_model:
        Override the llmlingua HuggingFace model id.
    min_keep_tokens:
        Minimum tokens kept per chunk (default 16).
    keep_code_blocks:
        Keep fenced code blocks atomically (default True).
    keep_addresses:
        Keep addresses intact and score them as high-signal (default True).

    Returns
    -------
    list[dict]
        New chunk dicts (input not mutated) with compressed ``content``/``text``
        plus ``compression_method`` (``llmlingua`` / ``extractive`` /
        ``unchanged``), ``tokens_before``, ``tokens_after`` and
        ``compression_tags`` (``["code"]`` / ``["address"]`` when kept).
    """
    counter = count_tokens or _estimate_tokens
    target = max(1, int(target_tokens))

    pairs: list[tuple[int, str]] = []
    for idx, chunk in enumerate(chunks):
        text = _get_text(chunk)
        if text is not None:
            pairs.append((idx, text))

    result: list[dict[str, Any]] = [dict(c) for c in chunks]
    if not pairs:
        return result

    totals = [counter(text) for _, text in pairs]
    total = sum(totals)
    if total <= target:
        for idx, text in pairs:
            before = counter(text)
            _annotate(result[idx], method="unchanged", before=before, after=before)
        return result

    floor = max(1, min(int(min_keep_tokens), target // max(1, len(pairs))))
    budgets = _distribute_budget(totals, target, floor)
    use_llm = bool(use_llmlingua) and LLMLINGUA_AVAILABLE

    for (idx, text), budget in zip(pairs, budgets, strict=True):
        entry = result[idx]
        before = counter(text)
        if before <= budget:
            _annotate(entry, method="unchanged", before=before, after=before)
            continue

        out: str
        method: str
        stats: dict[str, Any] = {"kept_code_blocks": 0, "kept_addresses": 0}
        if use_llm:
            try:
                out = _llmlingua_compress(text, budget, query, llmlingua_model)
                method = "llmlingua"
            except Exception as exc:
                logger.debug(
                    "prompt_compression: llmlingua compress failed (%s) — extractive fallback",
                    exc,
                )
                out, method, stats = _extractive_compress(
                    text, budget, counter, keep_code_blocks, keep_addresses
                )
        else:
            out, method, stats = _extractive_compress(
                text, budget, counter, keep_code_blocks, keep_addresses
            )

        after = counter(out)
        _annotate(entry, method=method, before=before, after=after, stats=stats)
        _set_text(entry, out)

    return result


def _extractive_compress(
    text: str,
    budget: int,
    count_tokens: Callable[[str], int],
    keep_code_blocks: bool,
    keep_addresses: bool,
) -> tuple[str, str, dict[str, Any]]:
    """Run the extractive compressor; returns ``(text, method, stats)``."""
    out, stats = _compress_text(
        text,
        budget,
        count_tokens=count_tokens,
        keep_code_blocks=keep_code_blocks,
        keep_addresses=keep_addresses,
    )
    return out, "extractive", stats


# ── "lost in the middle" inversion ────────────────────────────────────────

_SCORE_KEYS = (
    "rerank_score",
    "combined_score",
    "cross_score",
    "score",
    "rrf_score",
    "similarity",
    "bm25_score",
    "text_score",
)


def _chunk_score(chunk: dict[str, Any], score_key: str | None = None) -> float:
    """Best available relevance score (rerank first, retrieval after)."""
    keys = (score_key,) if score_key else _SCORE_KEYS
    for key in keys:
        val = chunk.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    meta = chunk.get("metadata") or {}
    for key in keys:
        val = meta.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def layout_context(
    chunks: Sequence[dict[str, Any]],
    query: str,
    *,
    top_chunks: int = 3,
    score_key: str | None = None,
) -> list[str]:
    """Order prompt parts to defeat "lost in the middle".

    Returns ``[top_chunk_1, ..., top_chunk_N, middle_chunk_1, ..., query]``:
    highest-relevance chunks at the top of the window, lower-relevance in the
    middle, user query LAST. Missing or tied scores keep original (retrieval)
    order via a stable sort.

    Parameters
    ----------
    chunks:
        Chunk dicts (post-rerank preferred: ``rerank_score`` /
        ``combined_score`` / ``cross_score``; retrieval keys also supported).
    query:
        The user query — always placed last in the returned layout.
    top_chunks:
        How many highest-relevance chunks go to the top of the window.
    score_key:
        Force a specific score key; defaults to auto-detection.
    """
    top_n = max(1, int(top_chunks))
    ordered = sorted(chunks, key=lambda c: _chunk_score(c, score_key), reverse=True)
    top = ordered[:top_n]
    middle = ordered[top_n:]
    parts = [_get_text(c) or "" for c in top + middle]
    parts = [p for p in parts if p]
    if query:
        parts.append(query)
    return parts


def pack_context(
    chunks: Sequence[dict[str, Any]],
    query: str,
    *,
    top_chunks: int = 3,
    score_key: str | None = None,
    separator: str = "\n\n",
) -> str:
    """Assemble the final prompt with the query LAST (lost-in-the-middle safe).

    Intended to run AFTER ``compress_context``::

        prompt = pack_context(compress_context(results, 2048, query=query), query)
    """
    return separator.join(layout_context(chunks, query, top_chunks=top_chunks, score_key=score_key))
