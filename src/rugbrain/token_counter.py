"""The Qwen3 tokenizer binding, and the density numbers derived from it.

Split out of :mod:`rugbrain.token_budget` so that module stays a budget
*policy* while this one owns a single job: knowing how many tokens a string
is, exactly, and how many characters to slice when the exact answer is
unavailable.

Qwen3-Embedding-8B and Qwen3-Reranker-8B share one tokenizer, so a count from
this module is valid for every consumer in RugBrain — embedding input,
cross-encoder input, MCP result payloads, briefing prompts. There is no
second definition anywhere in the codebase.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

_LOG = logging.getLogger(__name__)

#: Mean Qwen3 tokens per character across the measured corpus.
#: 111,131 tokens / 311,271 chars = 0.357, rounded down to 0.35. This is a
#: MEASUREMENT, not a bound — see :data:`DENSITY_MAX` for the number the
#: char-slice fallback actually relies on.
TOKENS_PER_CHAR: Final[float] = 0.35

#: Upper bound on tokens-per-character, used by the no-tokenizer fallback when
#: deciding how many characters to slice. Measured 2026-09-28 over 200-doc
#: Qdrant scrolls: the corpus mean is 0.35 tok/char and the DENSEST real
#: payload sampled is 0.77 (a news_v2 ticker/address-dense body, 389 tokens over
#: 507 chars). 0.8 is rounded up from that observed max, so it bounds every
#: document we actually store.
#:
#: This is why the corpus mean is not usable here. Slicing on 0.35 would have
#: over-sent dense payloads by 2.3x. Slicing on 0.8 under-sends prose
#: (deliberately: a budget is a ceiling, and erring low costs a little recall
#: rather than overrunning the cross-encoder's context).
DENSITY_MAX: Final[float] = 0.8

#: Snapshot of the tokenizer both model lanes run. Overridable for a
#: different revision without touching code.
TOKENIZER_PATH_ENV = "RUGBRAIN_TOKENIZER"
#: HF cache root for the Qwen3-Embedding-8B tokenizer. The snapshot hash is
#: resolved from ``refs/main`` at load time — a hardcoded hash goes stale the
#: moment the cache is re-pulled (observed 2026-10-15: pinned 5cf2132a… while
#: the live snapshot was 1d8ad4ca…, so every count silently fell back to the
#: ratio path).
_HF_CACHE_ROOT = "/root/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-8B"
DEFAULT_TOKENIZER_PATH = (
    f"{_HF_CACHE_ROOT}/snapshots/5cf2132abc99cad020ac570b19d031efec650f2b/tokenizer.json"
)


def _resolve_tokenizer_path() -> str:
    """Return the tokenizer path, following the HF cache's ``refs/main``.

    Prefers the live snapshot the cache points at; falls back to the pinned
    path so an explicit ``RUGBRAIN_TOKENIZER`` override or a non-HF layout
    still works.
    """
    try:
        with open(f"{_HF_CACHE_ROOT}/refs/main", encoding="utf-8") as fh:
            snapshot = fh.read().strip()
        if snapshot:
            candidate = f"{_HF_CACHE_ROOT}/snapshots/{snapshot}/tokenizer.json"
            if os.path.exists(candidate):
                return candidate
    except OSError:
        pass
    return DEFAULT_TOKENIZER_PATH


class TokenCounter:
    """Count and clamp tokens for one Qwen3 vocabulary.

    Binds the exact tokenizer when one is available and otherwise falls back
    to character slicing at ``max_tokens / DENSITY_MAX`` chars.

    Accounting is per-process and NOT thread-safe by design: it is called from
    asyncio handlers on one loop. A thread racing here would at worst
    mis-count a budget metric, never truncate wrongly, because truncation reads
    the text before writing the tally.
    """

    def __init__(self) -> None:
        self._encoder: Any | None = None
        self._probed = False

    def tokenizer(self) -> Any | None:
        """Load the Qwen3 tokenizer once; None when unavailable."""
        if self._probed:
            return self._encoder
        self._probed = True
        path = os.getenv(TOKENIZER_PATH_ENV) or _resolve_tokenizer_path()
        try:  # pragma: no cover - depends on the local HF cache
            from tokenizers import Tokenizer

            self._encoder = Tokenizer.from_file(path)
        except Exception as exc:
            _LOG.warning("tokenizer_load_failed path=%s err=%s", path, str(exc)[:120])
            self._encoder = None
        return self._encoder

    def count(self, text: str) -> int:
        """Exact Qwen3 token count when the tokenizer is present, else the ratio."""
        enc = self.tokenizer()
        if enc is not None:
            return len(enc.encode(text, add_special_tokens=False).ids)
        return int(len(text) * TOKENS_PER_CHAR)

    def clip(self, text: str, max_tokens: int) -> tuple[str, int, bool]:
        """Clamp `text` to `max_tokens`.

        Returns ``(text, tokens, truncated)``. With the tokenizer it cuts on a
        real token boundary, so a truncated passage ends at a sensible place
        instead of mid-word. The ratio fallback cuts at
        ``max_tokens / DENSITY_MAX`` characters.
        """
        if max_tokens <= 0 or not text:
            return text, 0, False
        enc = self.tokenizer()
        if enc is not None:
            ids = enc.encode(text, add_special_tokens=False).ids
            if len(ids) <= max_tokens:
                return text, len(ids), False
            return enc.decode(ids[:max_tokens]), max_tokens, True
        cap = int(max_tokens / DENSITY_MAX)
        if len(text) <= cap:
            return text, int(len(text) * TOKENS_PER_CHAR), False
        return text[:cap], max_tokens, True


__all__ = [
    "DEFAULT_TOKENIZER_PATH",
    "DENSITY_MAX",
    "TOKENIZER_PATH_ENV",
    "TOKENS_PER_CHAR",
    "TokenCounter",
]
