"""
RAG Search Engine — code-carrying chunk helpers.

Detection, re-ranking bias, fenced-block extraction and code-first answer
formatting shared by the code-query answer path.
"""

import re
from typing import Any

from .constants import _CODE_CHUNK_BONUS
from .query_intent import detect_coding_query

# Markers that indicate a chunk carries code (contract/function bodies,
# fenced blocks) rather than prose.
_CODE_MARKER_RE = re.compile(
    r"(?:"
    r"pragma\s+solidity|contract\s+\w+|interface\s+\w+|library\s+\w+|"
    r"function\s+\w+|modifier\s+\w+|constructor\s*\(|"
    r"event\s+\w+|error\s+\w+|struct\s+\w+|enum\s+\w+|"
    r"pub\s+fn\b|\bfn\s+\w+|impl\s+\w+|trait\s+\w+|"
    r"\bmod\s+\w+|fun\s+\w+|use\s+\w+::|use\s+anchor|"
    r"\bdef\s+\w+|class\s+\w+|import\s+.*\bfrom\b|```"
    r")",
    re.IGNORECASE,
)

# Fenced markdown code blocks (```lang … ``` or ~~~ … ~~~), language-tagged.
_FENCED_BLOCK_RE = re.compile(r"^([`~]{3,})\s*([\w.+-]*)\s*$(.*?)^\1\s*$", re.MULTILINE | re.DOTALL)


def _is_code_result(result: dict[str, Any]) -> bool:
    """True when *result* carries code content or code-typed metadata."""
    metadata = result.get("metadata") or {}
    doc_type = str(metadata.get("doc_type", "") or "").lower()
    if doc_type in ("contract_code", "code", "solidity", "rust", "solana"):
        return True
    if result.get("has_code_snippet") or result.get("chunk_type") == "code":
        return True
    return bool(_CODE_MARKER_RE.search((result.get("content") or "").lower()))


def bias_toward_code_chunks(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Re-rank *results* so code-carrying chunks float above prose.

    Only applied for code-intent queries (``detect_coding_query``) — non-code
    queries return *results* unchanged (behavior-preserving). A small bonus
    (``_CODE_CHUNK_BONUS``) is added to the similarity score of code chunks,
    and the sort is stable so equal scores keep the retrieval order.
    """
    if not results or not detect_coding_query(query):
        return results

    def _score(r: dict[str, Any]) -> float:
        base = (
            r.get("similarity")
            if r.get("similarity") is not None
            else r.get("rrf_score", 0.0) or 0.0
        )
        return float(base) + (_CODE_CHUNK_BONUS if _is_code_result(r) else 0.0)  # type: ignore[arg-type]

    return sorted(results, key=_score, reverse=True)


def extract_code_blocks(content: str) -> list[dict[str, str]]:
    """Extract fenced code blocks from *content*, intact and language-tagged.

    Each block keeps its original fence marker + language tag so the answer
    path can surface code verbatim instead of flattening it into prose.
    Returns ``[{"language", "code", "fence"}, ...]`` (empty when prose-only).
    """
    blocks: list[dict[str, str]] = []
    for m in _FENCED_BLOCK_RE.finditer(content or ""):
        blocks.append(
            {
                "language": (m.group(2) or "").strip(),
                "code": (m.group(3) or "").strip("\n"),
                "fence": m.group(1),
            }
        )
    return blocks


def format_code_answer(content: str, max_chars: int = 8000) -> str:
    """Return *content* with fenced code blocks surfaced intact.

    Rebuilds each fenced block with its original fence + language tag so
    downstream answer formatting never flattens code into prose. Content
    without fenced code is returned unchanged (trimmed to *max_chars*).
    """
    text = (content or "").strip()
    if not text:
        return ""
    if not _FENCED_BLOCK_RE.search(text):
        return text[:max_chars]
    out: list[str] = []
    pos = 0
    for m in _FENCED_BLOCK_RE.finditer(text):
        prefix = text[pos : m.start()].rstrip()
        if prefix:
            out.append(prefix)
        fence = m.group(1)
        lang = (m.group(2) or "").strip()
        code = (m.group(3) or "").strip("\n")
        out.append(f"{fence}{lang}\n{code}\n{fence}")
        pos = m.end()
    tail = text[pos:].lstrip("\n")
    if tail:
        out.append(tail)
    return "\n\n".join(out)[:max_chars]
