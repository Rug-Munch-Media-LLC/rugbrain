"""Multi-level news deduplication for the RugBrain RAG pipeline.

Levels (checked in order, all fail-open — a backend outage never blocks
ingestion):

  Level 1 — URL dedup:        exact URL hash in Redis ZSET ``rag:dedup:urls``
                              (member = sha256(url), score = unix ts, 30-day
                              per-member TTL enforced by lazy pruning).
  Level 2 — Title similarity: embed the title, search the Qdrant collection,
                              cosine similarity >= 0.95 → duplicate.
  Level 3 — Content hash:     ``content_fingerprint()`` (boilerplate stripped,
                              first 1000 chars, whitespace normalized) stored
                              in Redis ZSET ``rag:dedup:content:{collection}``.
  Level 4 — Entity overlap:   crypto entities (tokens, protocols, people,
                              addresses) overlap coefficient > 0.8 AND both
                              articles published within 6 hours → same story.

CryptoPanic-style grouping: the first article of a story receives a stable
``group_id`` (``grp:{sha256(url)[:12]}``). Duplicate variations are blocked
from ingestion but recorded as members of the existing group, so a story is
represented once in the corpus while every source URL maps back to its group.

Fail-open contract: every public check catches backend exceptions (Redis
auth/down, Qdrant unreachable, embedder unavailable) and returns
"not a duplicate" so ingestion always proceeds when dedup can't decide.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from rugbrain._logging import get_logger

log = get_logger(__name__)

# ── Tuning knobs ─────────────────────────────────────────────────────────

URL_DEDUP_TTL_DAYS = 30  # per-URL-hash retention
CONTENT_DEDUP_TTL_DAYS = 30  # per-fingerprint retention
TITLE_SIM_THRESHOLD = 0.95  # Level 2: cosine similarity for "same title"
ENTITY_OVERLAP_THRESHOLD = 0.8  # Level 4: min overlap coefficient
ENTITY_WINDOW_HOURS = 6  # Level 4: max age gap between the two articles
GROUP_MEMBERS_TTL_DAYS = 30

_URL_ZSET = "rag:dedup:urls"
_CONTENT_ZSET_PREFIX = "rag:dedup:content:"
_URL_GROUP_HASH = "rag:dedup:url:group"
_CONTENT_GROUP_HASH_PREFIX = "rag:dedup:content:group:"
_GROUP_MEMBERS_PREFIX = "rag:dedup:group:members:"

# ── Redis access (fail-open) ─────────────────────────────────────────────

_redis_client: Any | None = None


def _get_cache() -> Any | None:
    """Resolve the shared cache backend (in-memory or Redis), never raises.

    ``get_cache()`` returns a backend (or a fail-open in-memory fallback)
    and never raises; this wrapper only caches the result.
    """
    global _redis_client
    if _redis_client is None:
        try:
            from rugbrain.storage.cache import get_cache

            _redis_client = get_cache()
        except Exception as e:  # pragma: no cover - defensive
            log.debug("dedup_redis_init_failed err=%s", e)
            _redis_client = None
    return _redis_client


def _reset_redis_client_for_tests() -> None:  # pragma: no cover - test helper
    """Reset the cached Redis client (used by unit tests with fakeredis)."""
    global _redis_client
    _redis_client = None


def _now_ts() -> int:
    return int(time.time())


# ── Shared helpers ───────────────────────────────────────────────────────


def url_hash(url: str) -> str:
    """Stable short hash of a URL for dedup keys (matches crawler doc-id style)."""
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:32]


def doc_id_hash(doc_id: str) -> str:
    """Stable short hash of an external doc-id for exact doc-id dedup.

    Mirrors ``url_hash`` so doc-ids and URLs share one key space; use it for
    crawler-returned ``doc_id`` values that are stable across re-ingestion.
    """
    return hashlib.sha256((doc_id or "").encode("utf-8", errors="ignore")).hexdigest()[:32]


def _prune_zset(r: Any, key: str, ttl_days: int) -> None:
    """Remove members older than *ttl_days* from a timestamp-scored ZSET."""
    try:
        r.zremrangebyscore(key, 0, _now_ts() - ttl_days * 86400)
    except Exception as e:
        log.debug("dedup_prune_failed key=%s err=%s", key, e)


# ── Level 1: URL dedup ───────────────────────────────────────────────────


def is_duplicate_url(url: str) -> bool:
    """True when this exact URL was ingested within the 30-day window.

    Fail-open: Redis down/auth-failure → False (allow through).
    """
    if not url:
        return False
    try:
        r = _get_cache()
        if r is None:
            log.debug("dedup_url_redis_unavailable url=%s allow=True", url)
            return False
        _prune_zset(r, _URL_ZSET, URL_DEDUP_TTL_DAYS)
        member = url_hash(url)
        score = r.zscore(_URL_ZSET, member)
        if score is None:
            return False
        # Lazy per-member TTL: stale member → treat as new.
        if float(score) < _now_ts() - URL_DEDUP_TTL_DAYS * 86400:
            r.zrem(_URL_ZSET, member)
            return False
        return True
    except Exception as e:
        log.debug("dedup_url_check_failed url=%s err=%s allow=True", url, e)
        return False


def mark_url_ingested(url: str) -> None:
    """Record a URL as ingested (idempotent). Fail-open on Redis errors."""
    if not url:
        return
    try:
        r = _get_cache()
        if r is None:
            return
        r.zadd(_URL_ZSET, {url_hash(url): _now_ts()})
    except Exception as e:
        log.debug("dedup_url_mark_failed url=%s err=%s", url, e)


def lookup_group_by_url(url: str) -> str | None:
    """Return the story group_id a URL was mapped to, if any."""
    try:
        r = _get_cache()
        if r is None:
            return None
        val = r.hget(_URL_GROUP_HASH, url_hash(url))
        return str(val) if val else None
    except Exception as e:
        log.debug("dedup_url_group_lookup_failed err=%s", e)
        return None


# ── Level 3: content fingerprint ────────────────────────────────────────

# Boilerplate lines to strip before fingerprinting (footer/nav/ads/CTAs).
_BOILERPLATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?i)^\s*(subscribe|follow us|follow me|sign up|newsletter|join our)",
    ),
    re.compile(r"(?i)^\s*(cookie|cookies|privacy policy|terms of service|terms of use)"),
    re.compile(r"(?i)^\s*(advertisement|advertising|sponsored|paid promotion|press release)"),
    re.compile(r"(?i)^\s*(related articles|related news|you might also like|read more|more stories)"),
    re.compile(r"(?i)^\s*(all rights reserved|©|copyright|\d{4}\s+[a-z0-9 ]+\.\s+all rights)"),
    re.compile(r"(?i)^\s*(share this|tweet this|share on|comment on|email us|contact us)"),
    re.compile(r"(?i)^\s*(home|about|about us|careers|advertise|contact|help|faq|sitemap)$"),
    re.compile(r"^\s*\[(?:Image|Photo|Video)(?::.*?)?\]\s*$"),
    re.compile(r"^\s*https?://\S+\s*$"),  # bare URL lines (nav/footer links)
    re.compile(r"^\s*[-—|•]+\s*$"),
]


def content_fingerprint(text: str) -> str:
    """Stable content fingerprint that survives minor formatting changes.

    1. Strip boilerplate lines (footer, nav, ads, CTAs).
    2. Take the first 1000 chars of the remaining body.
    3. Collapse whitespace + lowercase.
    4. SHA-256.

    Returns the hex digest (deterministic for semantically identical bodies).
    """
    if not text:
        return hashlib.sha256(b"").hexdigest()
    lines: list[str] = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        if any(p.search(stripped) for p in _BOILERPLATE_PATTERNS):
            continue
        lines.append(stripped)
    body = " ".join(lines)[:1000]
    norm = re.sub(r"\s+", " ", body).strip().lower()
    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()


# ── Address normalization ────────────────────────────────────────────────

_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
# Solana base58 addresses (no 0/I/O/l, case-sensitive by design).
_SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_valid_evm_address(address: str) -> bool:
    """True when *address* looks like a 0x-prefixed 40-hex EVM address."""
    return bool(address and _EVM_ADDRESS_RE.match(address.strip()))


def to_checksum_address(address: str) -> str:
    """EIP-55 checksum an EVM address (idempotent).

    Falls back to lowercase when ``eth_utils`` is unavailable so dedup never
    hard-depends on the optional library. Non-EVM-shaped input is returned
    unchanged (fail-safe, never raises).
    """
    if not is_valid_evm_address(address):
        return address
    try:
        from eth_utils import to_checksum_address as _eth_checksum  # type: ignore[import-not-found]

        return cast(str, _eth_checksum(address))
    except Exception:  # pragma: no cover - optional dependency fallback
        return address.lower()


def normalize_address(address: str, chain: str = "evm") -> str:
    """Normalize *address* for dedup keying by chain family.

    * EVM (default, plus eth/base/bsc/… aliases): EIP-55 checksum so the same
      wallet in mixed casing maps to one key.
    * Solana (``sol``/``solana``): base58 is case-sensitive — preserved
      verbatim (whitespace trimmed only).
    * Unknown chain: trimmed as-is (fail-safe, never raises).
    """
    if not address:
        return ""
    addr = address.strip()
    c = (chain or "evm").strip().lower()
    if c in ("sol", "solana"):
        return addr
    if c in (
        "evm",
        "eth",
        "ethereum",
        "base",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "avalanche",
        "fantom",
        "gnosis",
    ):
        return to_checksum_address(addr)
    return addr


# ── Content hash (exact-block dedup) ─────────────────────────────────────

# Reuse the Level-3 boilerplate strippers (defined above) so content hashing
# and fingerprinting agree on what "real content" means.


def normalize_text(text: str) -> str:
    """Canonical text form: boilerplate stripped, whitespace collapsed, lower.

    Unlike ``content_fingerprint`` (which truncates to the first 1000 chars),
    ``normalize_text`` keeps the FULL body — use it for exact/near-identical
    block detection where truncation would be lossy.
    """
    if not text:
        return ""
    lines: list[str] = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        if any(p.search(stripped) for p in _BOILERPLATE_PATTERNS):
            continue
        lines.append(stripped)
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lower()


def content_hash(text: str) -> str:
    """SHA-256 of the fully normalized text — identical blocks collapse.

    Feed ``content_hash`` into the existing ``is_duplicate_content`` /
    ``mark_content_ingested`` Redis ZSETs to block re-ingestion of
    byte-identical (modulo whitespace/case/boilerplate) content.
    """
    return hashlib.sha256(normalize_text(text).encode("utf-8", errors="ignore")).hexdigest()


def tokenize(text: str) -> set[str]:
    """Normalized word tokens for similarity scoring (lowercased alnum runs)."""
    return set(re.findall(r"[a-z0-9]+", normalize_text(text)))


def near_duplicate(a: str, b: str, threshold: float = 0.9) -> bool:
    """Pure token-set Jaccard similarity helper for optional soft-dedup.

    Returns True when the token-set Jaccard similarity
    ``|A ∩ B| / |A + B| >= threshold`` — spelled out: size of the intersection
    over size of the union of normalized tokens meets the threshold.
    Empty/entity-free inputs return False (cannot decide → fail-open, matching
    the module's other checks). No network, no Redis — safe for inline
    candidate filters.
    """
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    return jaccard >= threshold


def is_duplicate_content(fp: str, collection: str) -> bool:
    """True when this content fingerprint was ingested within the TTL window.

    Fail-open: Redis unavailable → False (allow through).
    """
    if not fp:
        return False
    try:
        r = _get_cache()
        if r is None:
            log.debug("dedup_content_redis_unavailable allow=True")
            return False
        key = f"{_CONTENT_ZSET_PREFIX}{collection}"
        _prune_zset(r, key, CONTENT_DEDUP_TTL_DAYS)
        score = r.zscore(key, fp)
        if score is None:
            return False
        if float(score) < _now_ts() - CONTENT_DEDUP_TTL_DAYS * 86400:
            r.zrem(key, fp)
            return False
        return True
    except Exception as e:
        log.debug("dedup_content_check_failed err=%s allow=True", e)
        return False


def mark_content_ingested(fp: str, collection: str) -> None:
    """Record a content fingerprint as ingested. Fail-open on Redis errors."""
    if not fp:
        return
    try:
        r = _get_cache()
        if r is None:
            return
        r.zadd(f"{_CONTENT_ZSET_PREFIX}{collection}", {fp: _now_ts()})
    except Exception as e:
        log.debug("dedup_content_mark_failed err=%s", e)


def lookup_group_by_fp(fp: str, collection: str) -> str | None:
    """Return the story group_id a fingerprint was mapped to, if any."""
    try:
        r = _get_cache()
        if r is None:
            return None
        val = r.hget(f"{_CONTENT_GROUP_HASH_PREFIX}{collection}", fp)
        return str(val) if val else None
    except Exception as e:
        log.debug("dedup_content_group_lookup_failed err=%s", e)
        return None


# ── Level 2: title similarity (Qdrant) ──────────────────────────────────


async def is_duplicate_by_title(title: str, collection: str = "news_v2") -> bool:
    """True when an existing article has a nearly identical title.

    Embeds the title and searches the Qdrant collection; any hit with
    cosine similarity >= ``TITLE_SIM_THRESHOLD`` (0.95) counts as a
    duplicate. Fail-open: embedder/Qdrant unavailable → False.
    """
    try:
        hits = await _title_search(title, collection, limit=5)
    except Exception as e:
        log.debug("dedup_title_check_failed title=%r err=%s allow=True", title, e)
        return False
    return any(float(h.score) >= TITLE_SIM_THRESHOLD for h in hits)


async def _title_search(title: str, collection: str, limit: int = 5) -> list[Any]:
    """Embed *title* and vector-search the Qdrant collection.

    Reuses the engine's fail-open ``_qdrant_search`` (handles legacy + named
    vectors, returns [] when Qdrant is unreachable). Returns engine ``Hit``
    objects with ``{doc_id, score, text, metadata}``.
    """
    if not title or not title.strip():
        return []
    from rugbrain.core.engine import _qdrant_search
    from rugbrain.embed.embeddings import get_embedding

    vec = await get_embedding(title)
    return await _qdrant_search(vec, collection, limit=limit, score_threshold=0.0)


# ── Level 4: entity overlap ─────────────────────────────────────────────

# Curated crypto vocabulary (lowercased for matching). Kept dependency-free.
KNOWN_TOKENS: set[str] = {
    "btc",
    "eth",
    "sol",
    "xrp",
    "doge",
    "pepe",
    "shib",
    "usdt",
    "usdc",
    "bnb",
    "ada",
    "dot",
    "avax",
    "matic",
    "link",
    "uni",
    "aave",
    "dai",
    "near",
    "apt",
    "arb",
    "op",
    "ton",
    "trx",
    "ltc",
    "bch",
    "xlm",
    "atom",
    "icp",
    "fil",
    "hbar",
    "vet",
    "theta",
    "egld",
    "ftm",
    "algo",
    "sui",
    "sei",
    "injective",
    "jup",
    "bonk",
    "wld",
    "ordi",
    "sats",
    "pyth",
}

KNOWN_PROTOCOLS: set[str] = {
    "uniswap",
    "aave",
    "compound",
    "lido",
    "eigenlayer",
    "jupiter",
    "raydium",
    "meteora",
    "curve",
    "balancer",
    "makerdao",
    "sky",
    "pendle",
    "ethena",
    "ondo",
    "morpho",
    "spark",
    "fluid",
    "gmx",
    "hyperliquid",
    "dydx",
    "perp",
    "1inch",
    "paraswap",
    "0x",
    "opensea",
    "blur",
    "magiceden",
    "tensor",
    "drift",
    "mango",
    "margin",
    "jito",
    "sanctum",
    "solanafm",
    "helius",
    "chainlink",
    "pyth",
    "wormhole",
    "layerzero",
    "arbitrum",
    "optimism",
    "base",
    "polygon",
    "avalanche",
    "binance",
    "coinbase",
    "kraken",
    "bybit",
    "okx",
    "kucoin",
    "bitfinex",
    "robinhood",
    "circle",
}

KNOWN_PEOPLE: set[str] = {
    "vitalik",
    "vitalik buterin",
    "cz",
    "changpeng zhao",
    "sbf",
    "sam bankman-fried",
    "gary gensler",
    "brian armstrong",
    "michael saylor",
    "justin sun",
    "daniel larimer",
    "anatoly yakovenko",
    "raj goyal",
    "naval ravikant",
    "elon musk",
    "jack dorsey",
    "brad garlinghouse",
    "charles hoskinson",
    "eric adams",
    "jerome powell",
    "jamie dimon",
}

_TOKEN_RE = re.compile(r"\$([a-zA-Z]{2,12})\b")
_ADDRESS_EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_ADDRESS_SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def extract_crypto_entities(text: str) -> set[str]:
    """Extract a normalized set of crypto entities from *text*.

    Entities: ``$TICKER`` symbols, known token symbols, known protocols,
    known people, EVM addresses, and Solana addresses. Everything is
    lowercased so comparisons are case-insensitive. Returns an empty set
    when nothing recognizable is found (callers treat that as "can't
    decide" → fail-open).
    """
    if not text:
        return set()
    lowered = text.lower()
    entities: set[str] = set()

    for m in _TOKEN_RE.finditer(text):
        entities.add(m.group(1).lower())

    for tok in KNOWN_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", lowered):
            entities.add(tok)

    for proto in KNOWN_PROTOCOLS:
        if re.search(rf"\b{re.escape(proto)}\b", lowered):
            entities.add(proto)

    for person in KNOWN_PEOPLE:
        if re.search(rf"\b{re.escape(person)}\b", lowered):
            entities.add(person)

    entities.update(_ADDRESS_EVM_RE.findall(text.lower()))
    entities.update(_ADDRESS_SOL_RE.findall(text))

    return entities


def is_duplicate_by_entities(text_a: str, text_b: str, threshold: float = ENTITY_OVERLAP_THRESHOLD) -> bool:
    """True when *text_a* and *text_b* share >80% of crypto entities.

    Overlap coefficient = |A ∩ B| / min(|A|, |B|). When either side has no
    entities we cannot decide → False (fail-open).
    """
    ent_a = extract_crypto_entities(text_a)
    ent_b = extract_crypto_entities(text_b)
    if not ent_a or not ent_b:
        return False
    overlap = len(ent_a & ent_b) / min(len(ent_a), len(ent_b))
    return overlap >= threshold


def _parse_ts(value: str) -> datetime | None:
    """Parse a published_at string (ISO or RSS) to an aware datetime."""
    if not value:
        return None
    v = value.strip()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt is None:
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(v)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _within_window(ts_a: datetime | None, ts_b: datetime | None, hours: int = ENTITY_WINDOW_HOURS) -> bool:
    """True when both timestamps exist and are within *hours* of each other."""
    if ts_a is None or ts_b is None:
        return False
    return abs((ts_a - ts_b).total_seconds()) <= hours * 3600


# ── Grouping (CryptoPanic-style) ────────────────────────────────────────


def new_group_id(url: str) -> str:
    """Create a stable group_id for a new story from its first URL."""
    return f"grp:{url_hash(url)}"


def record_group_membership(group_id: str, url: str, collection: str) -> None:
    """Map a URL to its story group and add it to the group's member set."""
    try:
        r = _get_cache()
        if r is None:
            return
        pipe = r.pipeline()
        pipe.hset(_URL_GROUP_HASH, url_hash(url), group_id)
        pipe.sadd(f"{_GROUP_MEMBERS_PREFIX}{group_id}", url)
        pipe.expire(f"{_GROUP_MEMBERS_PREFIX}{group_id}", GROUP_MEMBERS_TTL_DAYS * 86400)
        pipe.execute()
    except Exception as e:
        log.debug("dedup_group_membership_failed err=%s", e)


def record_fp_group(fp: str, group_id: str, collection: str) -> None:
    """Map a content fingerprint to its story group."""
    try:
        r = _get_cache()
        if r is None:
            return
        r.hset(f"{_CONTENT_GROUP_HASH_PREFIX}{collection}", fp, group_id)
    except Exception as e:
        log.debug("dedup_fp_group_failed err=%s", e)


# ── Orchestrator ────────────────────────────────────────────────────────


@dataclass
class DedupResult:
    """Outcome of the multi-level dedup check.

    Attributes:
        is_duplicate: True when any level blocked ingestion.
        level: Which level fired: ``url`` | ``title`` | ``content`` | ``entity``.
        group_id: The story group this article belongs to (existing group on
            duplicate, or a freshly generated one when ``is_duplicate`` is
            False and no prior group was found).
        matched_doc_id: The doc_id of the existing article that matched.
    """

    is_duplicate: bool = False
    level: str | None = None
    group_id: str | None = None
    matched_doc_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


async def check_duplicates(
    url: str,
    title: str,
    content: str,
    collection: str = "news_v2",
    published_at: str = "",
) -> DedupResult:
    """Run all four dedup levels in order; stop at the first duplicate.

    Order: URL (cheap) → content fingerprint (cheap) → title (embed) →
    entities (uses the same Qdrant hits for candidate articles).

    Every level fails open: backend errors are logged at DEBUG and treated
    as "not a duplicate", so ingestion is never blocked by dedup outages.

    Args:
        url: Canonical article URL.
        title: Article title.
        content: Article body (markdown/text).
        collection: RAG collection (default ``news_v2``).
        published_at: ISO/RSS timestamp of the article; used for the Level 4
            six-hour window check. Empty → window check skipped.

    Returns:
        A ``DedupResult``. When ``is_duplicate`` is True the caller should
        NOT ingest; ``group_id`` carries the existing story group.
    """
    # Level 1: URL
    if is_duplicate_url(url):
        group = lookup_group_by_url(url)
        log.debug(
            "dedup_level=url url=%s group=%s",
            url,
            group,
        )
        return DedupResult(is_duplicate=True, level="url", group_id=group)

    # Level 3: content fingerprint (cheap, no network)
    fp = content_fingerprint(content)
    if is_duplicate_content(fp, collection):
        group = lookup_group_by_fp(fp, collection)
        log.debug(
            "dedup_level=content fp=%s group=%s",
            fp[:12],
            group,
        )
        return DedupResult(is_duplicate=True, level="content", group_id=group, details={"fp": fp})

    # Level 2 + Level 4 share a single Qdrant title search.
    hits: list[Any] = []
    try:
        hits = await _title_search(title, collection, limit=5)
    except Exception as e:
        log.debug("dedup_title_search_failed err=%s allow=True", e)
        hits = []

    # Level 2: title similarity
    for h in hits:
        if float(h.score) >= TITLE_SIM_THRESHOLD:
            group = _group_from_hit(h) or lookup_group_by_url(str(h.metadata.get("source_url", "")))
            log.debug("dedup_level=title url=%s score=%s group=%s", url, round(float(h.score), 4), group)
            return DedupResult(
                is_duplicate=True,
                level="title",
                group_id=group,
                matched_doc_id=h.doc_id,
                details={"score": float(h.score)},
            )

    # Level 4: entity overlap against candidates within the time window
    new_ts = _parse_ts(published_at)
    for h in hits:
        cand_ts = _parse_ts(str(h.metadata.get("published_at") or h.metadata.get("date") or ""))
        if not _within_window(new_ts, cand_ts):
            continue
        if is_duplicate_by_entities(content, str(h.text or "")):
            group = _group_from_hit(h) or lookup_group_by_url(str(h.metadata.get("source_url", "")))
            log.debug(
                "dedup_level=entity url=%s matched=%s group=%s",
                url,
                h.doc_id,
                group,
            )
            return DedupResult(
                is_duplicate=True,
                level="entity",
                group_id=group,
                matched_doc_id=h.doc_id,
            )

    # No duplicate found → fresh story (or a variation we can't prove).
    return DedupResult(is_duplicate=False, group_id=new_group_id(url), details={"fp": fp})


def _group_from_hit(hit: Any) -> str | None:
    """Read a group_id from a search hit's metadata, if present."""
    try:
        g = hit.metadata.get("group_id")
        return str(g) if g else None
    except Exception:
        return None


__all__ = [
    "DedupResult",
    "check_duplicates",
    "content_fingerprint",
    "content_hash",
    "doc_id_hash",
    "extract_crypto_entities",
    "is_duplicate_by_entities",
    "is_duplicate_by_title",
    "is_duplicate_content",
    "is_duplicate_url",
    "is_valid_evm_address",
    "lookup_group_by_fp",
    "lookup_group_by_url",
    "mark_content_ingested",
    "mark_url_ingested",
    "near_duplicate",
    "new_group_id",
    "normalize_address",
    "normalize_text",
    "record_fp_group",
    "record_group_membership",
    "to_checksum_address",
    "tokenize",
    "url_hash",
]
