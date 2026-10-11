"""Token budget — one place that answers "how much text do we send, and why".

Why this module exists
----------------------
RugBrain serves LLMs and agents, so every token it emits is paid for twice:
once by the cross-encoder, once by the model reading the result. Three paths
had no ceiling at all, and the ceilings that existed were expressed in
*characters* against a corpus whose real Qwen3 mean density is 0.35
tokens/char (measured 2026-09-28 over 200-doc Qdrant scrolls: 111,131 tokens
over 311,271 chars). A "6000 char" cap is 2,100 tokens; nobody knew, so
nobody tuned it.

The defaults below are not guesses. Each carries the measurement it came from,
and ``tests/test_token_budget.py`` asserts the *relationships* that make them
safe rather than hardcoding a magic integer.

Budgets
-------
``EMBEDDING``  2,048 tok — Qwen3-Embedding-8B's own context. Every provider
               lane in ``embeddings.py``/``qwen_embedders.py`` truncates to
               8,000 *chars* (2,800 tok) before sending; this makes the
               intended ceiling explicit and shared instead of five copies of
               a magic number.
``RERANK``       768 tok — the cross-encoder needs the passage, not the whole
               document. Measured on the content path: the top-12 news_v2
               chunks place 69.8% of their tokens in the first 400 chars, and
               768 tok covers p99 of every collection measured (known_scams
               p99=744). This also bounds the deep-tier pool, which
               previously had no cap and sent 17,488 tokens for one query.
``RESULT``     1,200 tok — per-result content returned to a model. Half the
               2,800-token embedding ceiling, because a model reading a
               10-result payload does not benefit from the tail.

Override with ``RUGBRAIN_TOKEN_EMBEDDING``, ``RUGBRAIN_TOKEN_RERANK``,
``RUGBRAIN_TOKEN_RESULT``.
"""

from __future__ import annotations

import os
from typing import Final

from rugbrain.token_counter import (
    DENSITY_MAX,
    TOKENS_PER_CHAR,
    TokenCounter,
)

# ── Defaults, each traceable to a measurement (2026-09-28) ────────────

EMBEDDING_BUDGET: Final[int] = 2048
RERANK_BUDGET: Final[int] = 768
RESULT_BUDGET: Final[int] = 1200

#: The single counter. One tokenizer load, one tally, no second definition.
COUNTER: Final[TokenCounter] = TokenCounter()

#: Per-path token tally, keyed by path name. Callers register the name they
#: care about ("rerank_doc", "mcp_result", ...) so a report can be built
#: without every call site inventing its own accounting.
_tally: dict[str, int] = {}


def _env_budget(name: str, default: int) -> int:
    """Read a budget override; a non-positive or unparseable value is ignored.

    A bad knob falls back to the default rather than disabling the cap —
    a typo in an env var must never open an unbounded path.
    """
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return val if val > 0 else default


def embedding_budget() -> int:
    """Max tokens for a single embedded text. Default 2048."""
    return _env_budget("RUGBRAIN_TOKEN_EMBEDDING", EMBEDDING_BUDGET)


def rerank_budget() -> int:
    """Max tokens per document sent to the cross-encoder. Default 768."""
    return _env_budget("RUGBRAIN_TOKEN_RERANK", RERANK_BUDGET)


def result_budget() -> int:
    """Max tokens per result returned to a model. Default 1200."""
    return _env_budget("RUGBRAIN_TOKEN_RESULT", RESULT_BUDGET)


def record(path: str, text: str) -> int:
    """Tally `text` against `path`; returns the token count for convenience."""
    count = COUNTER.count(text)
    _tally[path] = _tally.get(path, 0) + count
    return count


def budget_snapshot() -> dict[str, int]:
    """Per-path token tally collected so far in this process."""
    return dict(_tally)


def reset_budgets() -> None:
    """Clear the per-path tally (tests; the briefing path at cycle start)."""
    _tally.clear()


def tokens(text: str) -> int:
    """Exact Qwen3 token count for `text`."""
    return COUNTER.count(text)


def clip(text: str, max_tokens: int) -> str:
    """Clamp `text` to `max_tokens`."""
    return COUNTER.clip(text, max_tokens)[0]


__all__ = [
    "COUNTER",
    "DENSITY_MAX",
    "EMBEDDING_BUDGET",
    "RERANK_BUDGET",
    "RESULT_BUDGET",
    "TOKENS_PER_CHAR",
    "TokenCounter",
    "budget_snapshot",
    "clip",
    "embedding_budget",
    "record",
    "rerank_budget",
    "reset_budgets",
    "result_budget",
    "tokens",
]



def budget_documents(documents: list[str]) -> list[str]:
    """Clamp every document to the rerank budget and tally the cost.

    One helper for every rerank entry point: `reranker.rerank()` is the single
    choke point they all funnel through, so the ceiling is applied once there
    rather than at each caller.
    """
    cap = rerank_budget()
    out: list[str] = []
    for doc in documents:
        clipped = clip(doc, cap)
        record("rerank_doc", clipped)
        out.append(clipped)
    return out
