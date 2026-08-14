"""M3 RAG Chunking - crypto-aware, per-content-type chunking with token enforcement.

Why chunking matters:
- Embedding models have context limits (bge-m3 = 8192 tokens, but we
  chunk to 512 for granularity and to keep individual FAISS hits useful)
- Smaller chunks = better retrieval precision (less noise per result)
- Dedup via content SHA-256 prevents the same fact being ingested N times

Strategy (per 2026 RAG standards):
- Content-type-aware strategies:
    solidity     → function/modifier/event/constructor boundaries
    rust         → module/function boundaries (fn, mod, impl, struct, ...)
    whitepaper   → section-header aware, markdown tables kept intact
    audit        → finding-by-finding, severity markers preserved
    news         → paragraph-level, whole sentences preserved (lede intact)
    social       → one post = one chunk
    transaction  → line/field aware (structured data, fields not split)
    general      → langchain-style recursive split (paragraph→sentence→word)
- Hard token cap (default 512) enforced on every chunk before embedding via
  tiktoken, falling back to a word-count heuristic when tiktoken is absent.
- Quality score: (length / max_chunk) x (1 - special_char_ratio)  # noqa: RUF002
- Skip chunks < min_chars (likely noise)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugbrain._logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

log = get_logger(__name__)

DEFAULT_MAX_CHUNK = 512
DEFAULT_OVERLAP = 64
DEFAULT_MIN_CHARS = 32
DEFAULT_MAX_TOKENS = 512

_redis = None

# ── Content-type → strategy registry ──────────────────────────────────


@dataclass
class Chunk:
    """A piece of text ready to embed.

    Metadata carried per chunk:
        content_type: strategy used to produce this chunk
        source_url:   originating document URL (when known)
        timestamp:    ISO-8601 ingest time
        token_count:  estimated token count (tiktoken / heuristic)
    """

    text: str
    content_hash: str
    index: int
    quality: float = 1.0
    content_type: str = "general"
    source_url: str = ""
    timestamp: str = ""
    token_count: int = 0


def content_hash(text: str) -> str:
    """Stable SHA-256 of normalized text. Used for dedup."""
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()


# ── Token counting + truncation ───────────────────────────────────────

_tokenizer_cache: dict[str, Any] = {}


def _get_tokenizer() -> Any | None:
    """Lazily load the tiktoken cl100k_base tokenizer. Returns None if
    tiktoken is unavailable or fails to load (then we fall back to a
    word-count heuristic)."""
    if "tok" not in _tokenizer_cache:
        try:
            import tiktoken  # type: ignore[import-not-found]

            _tokenizer_cache["tok"] = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            log.debug("tokenizer_unavailable: %s", e)
            _tokenizer_cache["tok"] = None
    return _tokenizer_cache["tok"]


def estimate_tokens(text: str) -> int:
    """Estimate token count via tiktoken; fall back to a word-count heuristic."""
    if not text:
        return 0
    enc = _get_tokenizer()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # noqa: S110 — intentional best-effort
            pass
    # Word-count heuristic (~1.3 tokens/word avg for English); never 0.
    return max(1, len(text.split()))


def _enforce_token_limit(chunk: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Truncate a chunk to fit within ``max_tokens`` (default 512).

    Uses tiktoken when available; otherwise truncates by character count
    (~4 chars/token). Guarantees the chunk is safe to send to Infinity.
    """
    if not chunk:
        return chunk
    enc = _get_tokenizer()
    if enc is not None:
        try:
            tokens = enc.encode(chunk)
            if len(tokens) <= max_tokens:
                return chunk
            return str(enc.decode(tokens[:max_tokens]))
        except Exception:  # noqa: S110 — intentional best-effort
            pass
    words = chunk.split()
    if len(words) <= max_tokens:
        return chunk
    return chunk[: max_tokens * 4]


# ── Generic splitting helpers ─────────────────────────────────────────


def _recursive_split(
    text: str,
    separators: list[str],
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Langchain-style recursive splitter.

    Greedy-packs text into chunks of <= ``chunk_size`` chars, preferring the
    natural boundary separators in order (paragraph → sentence → word).
    ``chunk_overlap`` chars of the previous chunk are carried forward.
    """
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end == n:
            chunks.append(text[start:].strip())
            break
        window = text[start:end]
        split_at: int | None = None
        for sep in separators:
            if not sep:
                split_at = end
                break
            pos = window.rfind(sep)
            if pos > chunk_size * 0.5:
                split_at = start + pos + len(sep)
                break
        if split_at is None:
            split_at = end
        piece = text[start:split_at].strip()
        if piece:
            chunks.append(piece)
        next_start = max(split_at - chunk_overlap, start + 1)
        if next_start >= n:
            break
        start = next_start
    return [c for c in chunks if c.strip()]


def _pack_segments(segments: list[str], chunk_size: int) -> list[str]:
    """Greedy-pack text segments into chunks of <= ``chunk_size`` chars,
    joining with newlines so line structure (tables/code) is preserved."""
    chunks: list[str] = []
    cur = ""
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if len(cur) + len(seg) + 1 <= chunk_size:
            cur = (cur + "\n" + seg) if cur else seg
        else:
            if cur:
                chunks.append(cur)
            cur = seg
    if cur:
        chunks.append(cur)
    return chunks


def _split_boundaries(text: str, pattern: re.Pattern[str], chunk_size: int) -> list[str]:
    """Split ``text`` at regex boundary matches; each piece is one chunk.
    Oversized pieces fall back to recursive char splitting."""
    matches = list(pattern.finditer(text))
    if not matches:
        return _recursive_split(text, ["\n\n", "\n", " ", ""], chunk_size, 0)
    pieces: list[str] = []
    prev = 0
    for m in matches:
        pieces.append(text[prev : m.start()].strip())
        prev = m.start()
    pieces.append(text[prev:].strip())
    out: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        if len(piece) > chunk_size:
            out.extend(_recursive_split(piece, ["\n\n", "\n", " ", ""], chunk_size, 0))
        else:
            out.append(piece)
    return [c for c in out if c.strip()]


# ── Per-content-type strategies ───────────────────────────────────────


def _chunk_semantic(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """General fallback: langchain-style recursive split (paragraph → sentence → word)."""
    return _recursive_split(text, ["\n\n", "\n", ". ", " ", ""], chunk_size, chunk_overlap)


_SOLIDITY_BOUNDARY = re.compile(
    r"(?m)^\s*(?:function\s+\w+|modifier\s+\w+|event\s+\w+|constructor\s*\(|receive\s*\(|fallback\s*\()\b"
)
_SOLIDITY_COMMENT = re.compile(r"(?m)^\s*//.*$|/\*.*?\*/", re.DOTALL)


def _chunk_solidity(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Chunk at function boundaries. Max 512 tokens per chunk.

    Preserve: pragma, imports, contract declaration.
    Split at: function, modifier, event, constructor boundaries.
    Each function = one chunk (unless >512 tokens, then split at comment blocks).
    """
    matches = list(_SOLIDITY_BOUNDARY.finditer(text))
    if not matches:
        return _recursive_split(text, ["\n\n", "\n", " ", ""], chunk_size, 0)

    # Header chunk: pragma + imports + contract declaration (+ state vars).
    pieces: list[str] = []
    prev = 0
    for m in matches:
        pieces.append(text[prev : m.start()].strip())
        prev = m.start()
    pieces.append(text[prev:].strip())

    out: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        if len(piece) > chunk_size:
            # Split oversized units at comment-block boundaries so the hard
            # token cap doesn't truncate mid-statement.
            segments = [s for s in _SOLIDITY_COMMENT.split(piece) if s.strip()]
            out.extend(_pack_segments(segments, chunk_size))
        else:
            out.append(piece)
    return [c for c in out if c.strip()]


_RUST_BOUNDARY = re.compile(r"(?m)^\s*(?:pub\s+)?(?:fn|mod|impl|struct|enum|trait|type|const|static|union)\s+\w+")


def _chunk_rust(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Chunk at module/function boundaries (fn, mod, impl, struct, enum, trait)."""
    return _split_boundaries(text, _RUST_BOUNDARY, chunk_size)


_WHITEPAPER_HEADER = re.compile(r"(?m)^#{1,4}\s+.+$")


def _chunk_whitepaper(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Chunk at section headers. Tables stay in one chunk.

    Preserve: title + abstract as first chunk.
    Split at: markdown headers (#, ##, ###).
    """
    headers = list(_WHITEPAPER_HEADER.finditer(text))
    if not headers:
        # No headers: keep title + abstract as the lead paragraph, then recursive.
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paragraphs:
            return []
        no_headers_out = [paragraphs[0]]
        if len(paragraphs) > 1:
            no_headers_out.extend(_chunk_semantic("\n\n".join(paragraphs[1:]), chunk_size, chunk_overlap))
        return no_headers_out

    pieces: list[str] = []
    prev = 0
    for m in headers:
        pieces.append(text[prev : m.start()].strip())
        prev = m.start()
    pieces.append(text[prev:].strip())

    out: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        if len(piece) > chunk_size:
            out.extend(_chunk_table_aware(piece, chunk_size))
        else:
            out.append(piece)
    return [c for c in out if c.strip()]


def _looks_like_table(text: str) -> bool:
    """Heuristic: a markdown table has >= 2 lines containing '|'."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) >= 2 and sum("|" in ln for ln in lines) >= 2


def _chunk_table_aware(text: str, chunk_size: int) -> list[str]:
    """Split an oversized whitepaper section at paragraph breaks, keeping
    markdown tables intact (a table is only split at row boundaries, never
    inside a row, when the table alone exceeds chunk_size)."""
    segments: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_size or not _looks_like_table(para):
            segments.append(para)
            continue
        # Oversized table: split at row boundaries; keep header + separator rows.
        rows = para.splitlines()
        cur = ""
        for row in rows:
            candidate = (cur + "\n" + row) if cur else row
            if len(candidate) > chunk_size and cur:
                segments.append(cur)
                cur = row
            else:
                cur = candidate
        if cur:
            segments.append(cur)
    return _pack_segments(segments, chunk_size)


_AUDIT_BOUNDARY = re.compile(
    r"(?mi)^\s*(?:#{1,4}\s*|(?:finding|issue|vulnerability|risk)\b[^\n]*|\[(?:critical|high|medium|low|info)\s*\])"
)


def _attach_trailing_severity(subchunks: list[str]) -> list[str]:
    """Re-attach a trailing ``Severity: ...`` line to the previous sub-chunk
    so a finding's severity is never orphaned (and later dropped as noise)."""
    out: list[str] = []
    for c in subchunks:
        if c.startswith("Severity:") and out:
            out[-1] = out[-1] + "\n" + c
        else:
            out.append(c)
    return out


def _chunk_audit(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Chunk audit reports finding-by-finding; preserve severity markers."""
    matches = list(_AUDIT_BOUNDARY.finditer(text))
    if not matches:
        return _recursive_split(text, ["\n\n", "\n", ". ", " ", ""], chunk_size, 0)

    pieces: list[str] = []
    prev = 0
    for m in matches:
        pieces.append(text[prev : m.start()].strip())
        prev = m.start()
    pieces.append(text[prev:].strip())

    out: list[str] = []
    for piece in pieces:
        if not piece:
            continue
        if len(piece) > chunk_size:
            sub = _recursive_split(piece, ["\n\n", "\n", ". ", " ", ""], chunk_size, 0)
            out.extend(_attach_trailing_severity(sub))
        else:
            out.append(piece)
    return [c for c in out if c.strip()]


def _first_sentence(text: str) -> str:
    """First sentence (up to the first . ! ? or newline), used as the lede."""
    m = re.match(r"\s*([^.!?\n]+[.!?]?)", text)
    return m.group(1).strip() if m else text.strip()


def _chunk_news(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Paragraph-level chunking; never split mid-sentence.

    Preserve: the first sentence as a standalone lead chunk.
    """
    lead = _first_sentence(text)
    if not lead:
        return []
    body = text[len(lead) :].strip()
    chunks = [lead]
    if body:
        chunks.extend(_recursive_split(body, ["\n\n", "\n", ". ", " ", ""], chunk_size, chunk_overlap))
    return chunks


def _chunk_social(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """One social post = one chunk (content is short by nature)."""
    return [text.strip()] if text.strip() else []


def _chunk_transaction(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Structured data (transactions, token reports): preserve field lines."""
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[str] = []
    cur = ""
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if len(cur) + len(line) + 1 > chunk_size and cur:
            chunks.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        chunks.append(cur)
    return chunks


STRATEGIES: dict[str, Callable[[str, int, int], list[str]]] = {
    "solidity": _chunk_solidity,
    "rust": _chunk_rust,
    "whitepaper": _chunk_whitepaper,
    "audit": _chunk_audit,
    "news": _chunk_news,
    "social": _chunk_social,
    "transaction": _chunk_transaction,
    "general": _chunk_semantic,
}


# ── Source type mapping ───────────────────────────────────────────────

_SOURCE_TYPE_MAP: dict[str, str] = {
    "scanner": "transaction",
    "wallet": "transaction",
    "news": "news",
    "research": "whitepaper",
    "scam": "audit",
    "scam_pattern": "audit",
    "audit": "audit",
    "whitepaper": "whitepaper",
    "solidity": "solidity",
    "rust": "rust",
    "transaction": "transaction",
    "twitter": "social",
    "reddit": "social",
    "telegram": "social",
    "discord": "social",
    "social": "social",
}


def map_source_type_to_content_type(source_type: str) -> str:
    """Map an ingestion ``source_type`` to a chunking ``content_type``.

    Unknown / empty source types fall back to ``"general"``.
    """
    if not source_type:
        return "general"
    return _SOURCE_TYPE_MAP.get(source_type.lower(), "general")


# ── Orchestrator ──────────────────────────────────────────────────────


def chunk_text(
    text: str,
    content_type: str = "general",
    chunk_size: int = DEFAULT_MAX_CHUNK,
    chunk_overlap: int = DEFAULT_OVERLAP,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    source_url: str = "",
    timestamp: str = "",
) -> list[Chunk]:
    """Chunk text based on content type for optimal embedding.

    Each returned Chunk carries metadata: ``content_type``, ``source_url``,
    ``chunk_index`` (as ``index``), ``timestamp`` and ``token_count``. Every
    chunk is hard-capped at ``max_tokens`` (default 512) tokens before being
    handed to the embedding backend.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    # Short documents: single chunk (matches legacy behaviour, keeps metadata).
    if len(text) <= chunk_size:
        h = content_hash(text)
        ts = timestamp or datetime.now(UTC).isoformat()
        return [
            Chunk(
                text=text,
                content_hash=h,
                index=0,
                quality=_quality(text, chunk_size),
                content_type=content_type,
                source_url=source_url,
                timestamp=ts,
                token_count=estimate_tokens(text),
            )
        ]

    strategy = STRATEGIES.get(content_type, _chunk_semantic)
    raw = [c.strip() for c in strategy(text, chunk_size, chunk_overlap) if c.strip()]
    ts = timestamp or datetime.now(UTC).isoformat()

    out: list[Chunk] = []
    for i, c in enumerate(raw):
        enforced = _enforce_token_limit(c, max_tokens).strip()
        if not enforced or len(enforced) < min_chars:
            continue
        out.append(
            Chunk(
                text=enforced,
                content_hash=content_hash(enforced),
                index=i,
                quality=_quality(enforced, chunk_size),
                content_type=content_type,
                source_url=source_url,
                timestamp=ts,
                token_count=estimate_tokens(enforced),
            )
        )
    return out


def _quality(text: str, max_chunk: int) -> float:
    """Quick quality score 0-1. Penalize noise (high special-char ratio)."""
    if not text:
        return 0.0
    n = len(text)
    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
    length_score = min(1.0, n / max_chunk)
    special_score = max(0.0, 1.0 - (special / n))
    return round(0.7 * length_score + 0.3 * special_score, 3)


# ── Dedup via Redis ─────────────────────────────────────────────────
def is_duplicate(content_hash: str, collection: str) -> bool:
    """Has this exact content already been ingested into the collection?

    Fail-open: when Redis is unavailable (e.g. host-side cron without
    credentials) the check is skipped and the document proceeds to ingest,
    rather than crashing the whole sweep.
    """
    try:
        client = _get_cache()
        if client is None:
            log.warning("dedup_skipped_redis_unavailable collection=%s", collection)
            return False
        return bool(client.sismember(f"rag:hashes:{collection}", content_hash))
    except Exception as e:
        log.warning("dedup_check_failed: %s", e)
        return False


def mark_ingested(content_hash: str, collection: str) -> None:
    """Record that this content hash is now in the collection.

    Never raises: a missing Redis client (host-side run without auth) is
    logged and skipped so ingestion is not aborted mid-document.
    """
    try:
        client = _get_cache()
        if client is None:
            log.warning("dedup_mark_skipped_redis_unavailable collection=%s", collection)
            return
        client.sadd(f"rag:hashes:{collection}", content_hash)
    except (ConnectionError, OSError, TimeoutError) as e:
        log.debug("dedup_mark_failed: %s", e)


def _get_cache() -> Any:
    global _redis
    if _redis is None:
        from rugbrain.storage.cache import get_cache

        _redis = get_cache()
    return _redis
