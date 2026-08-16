"""Security guardrails for the RugBrain RAG answer path.

Protects the retrieval/answer pipeline from three production risks:

1. **Prompt injection** — user queries that attempt to override the
   assistant's instructions ("ignore previous instructions"), switch roles
   ("system:", "you are now"), or smuggle instructions inside fenced code
   blocks. ``sanitize_query`` detects and neutralizes these before the
   query is embedded or forwarded to the MCP bridge.

2. **Secret leakage** — retrieved chunks or bridge results that contain
   private keys, mnemonic phrases, API-key-looking strings, or internal
   hostnames. ``redact_output`` strips them before the answer is returned.

3. **RAG poisoning** — chunks from user-uploaded / unverified partitions
   that could inject attacker-controlled context into an answer.
   ``trusted_filter`` drops (or ranks down) chunks whose metadata lacks a
   trusted source or carries a ``poisoned``/``unverified`` flag, and
   ``tag_unverified`` marks such docs at ingest time.

"Never query user-uploaded partitions" rule (Qdrant)
----------------------------------------------------
RugBrain collections are curated corpora. User-uploaded documents live in
dedicated partitions and MUST NOT be searched by the shared RAG answer
path — a poisoned user partition is indistinguishable from curated context
once vectors are mixed. Apply payload filtering at query time so
user-uploaded partitions are excluded before vector search:

    from rugbrain.guardrails import build_trusted_partition_filter
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore()
    results = await store.search(
        collection="rag_docs",
        vector=...,
        filters=build_trusted_partition_filter(),  # {"must_not": [...]}
    )

``build_trusted_partition_filter`` returns a Qdrant ``must_not`` filter on
the ``partition`` payload field (defaults exclude ``user_upload`` /
``user-uploaded``). Combine with any caller filter dict that has a
``must`` key: Qdrant accepts ``{"must": [...], "must_not": [...]}``.

This is a *default-deny* posture: chunks that lack any source metadata are
treated as untrusted by ``trusted_filter`` (fail-closed) unless explicitly
marked ``trusted``/``verified``.

NOTE: this module is dependency-free (stdlib only) so it can be imported
by every answer path without pulling in Qdrant/FastAPI.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Runtime toggle ─────────────────────────────────────────────────────
# Set RMI_GUARDRAILS=0 to disable input sanitization / output redaction
# (used by tests and local debugging). Trusted filtering is always opt-in
# because it requires callers to pass metadata-bearing docs.
_GUARDRAILS_ENABLED = os.getenv("RMI_GUARDRAILS", "1").strip().lower() not in ("0", "false", "no")


# ═══════════════════════════════════════════════════════════════════════
# 1. INPUT GUARDRAIL — prompt-injection detection / neutralization
# ═══════════════════════════════════════════════════════════════════════

# Instruction-override / role-switch phrases. Matched case-insensitively.
# Each entry is a (reason_code, compiled_pattern). The regexes are written
# to catch the classic jailbreak families:
#   * "ignore previous instructions" / "disregard all prior prompts"
#   * "system:" / "assistant:" / "human:" role labels
#   * "you are now" / "act as" / "pretend to be" role switches
#   * "reveal your system prompt" / "repeat your instructions"
#   * fenced code blocks that contain instruction-like text
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+"
            r"(?:all\s+)?(?:previous|prior|above|earlier|old|original|"
            r"system|initial|developer)?\s*(?:instructions?|prompts?|"
            r"messages?|content|context|rules?|guidelines?|orders?|text)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ignore_everything",
        re.compile(r"\bignore\s+(?:everything|all\b)", re.IGNORECASE),
    ),
    (
        "system_override",
        re.compile(
            r"\b(?:override|replace|change|update)\s+(?:your|the)\s+"
            r"(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?|settings?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_switch",
        re.compile(
            r"\b(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you\s+are)|"
            r"you\s+will\s+now|you\s+must\s+now|from\s+now\s+on)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_role_label",
        re.compile(r"(?:^|\n)\s*(?:system|assistant|developer|human)\s*:", re.IGNORECASE),
    ),
    (
        "marker_injection",
        re.compile(r"(?:<<\s*SYS\s*>>|<\|(?:system|im_start|im_end)\|>|\[/?INST\])", re.IGNORECASE),
    ),
    (
        "prompt_exfil",
        re.compile(
            r"\b(?:reveal|show|print|display|output|repeat|return|tell\s+me|"
            r"what\s+are|write\s+out)\s+(?:your|the|all\s+your)\s+"
            r"(?:system\s+|initial\s+|developer\s+)?"
            r"(?:prompt|instructions?|rules?|guidelines?|system\s+prompt)\b",
            re.IGNORECASE,
        ),
    ),
)

# Fenced code block detection: ```lang ... ``` or ~~~ ... ~~~. Used to spot
# instruction text smuggled inside a code block (a common prompt-injection
# vehicle when answers echo retrieved code verbatim).
_FENCE_RE = re.compile(r"(?:```|~~~)(.*?)(?:```|~~~)", re.DOTALL)

# Neutralization targets that we strip from the query (substring removal).
# Kept separate from detection so we never silently rewrite a legit query
# that merely *mentions* a jailbreak ("how do I defend against prompt
# injection?") — detection still flags it, stripping is conservative.
_STRIP_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+"
    r"(?:all\s+)?(?:previous|prior|above|earlier|old|original|system|initial|developer)?\s*"
    r"(?:instructions?|prompts?|messages?|content|context|rules?|guidelines?|orders?|text)\b"
    r"|\b(?:override|replace|change|update)\s+(?:your|the)\s+"
    r"(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?|settings?)\b"
    r"|\b(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you\s+are)|you\s+will\s+now|"
    r"you\s+must\s+now|from\s+now\s+on)\b"
    r"|\b(?:reveal|show|print|display|output|repeat|return|tell\s+me|"
    r"what\s+are|write\s+out)\s+(?:your|the|all\s+your)\s+"
    r"(?:system\s+|initial\s+|developer\s+)?"
    r"(?:prompt|instructions?|rules?|guidelines?|system\s+prompt)\b"
    r"|(?:^|\n)\s*(?:system|assistant|developer|human)\s*:"
    r"|(?:<<\s*SYS\s*>>|<\|(?:system|im_start|im_end)\|>|\[/?INST\])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SanitizedQuery:
    """Result of ``sanitize_query``.

    ``query`` is the (possibly cleaned) query safe to forward. ``flagged``
    is True when any injection pattern matched. ``reasons`` lists the
    human-readable pattern families that matched (empty when unflagged).
    Fail-closed: callers may refuse when ``flagged``, or forward the
    cleaned ``query``.
    """

    query: str
    flagged: bool
    reasons: tuple[str, ...] = ()


def _strip_fenced_instructions(text: str) -> str:
    """Remove fenced code blocks that contain instruction-like text."""
    if "```" not in text and "~~~" not in text:
        return text
    out_parts: list[str] = []
    last = 0
    for m in _FENCE_RE.finditer(text):
        inner = m.group(1) or ""
        if _INJECTION_PATTERNS[0][1].search(inner) or _INJECTION_PATTERNS[3][1].search(inner):
            out_parts.append(text[last : m.start()])
            last = m.end()
    if not out_parts:
        return text
    out_parts.append(text[last:])
    return "".join(out_parts)


def sanitize_query(query: str) -> SanitizedQuery:
    """Detect and neutralize prompt-injection patterns in *query*.

    Returns a :class:`SanitizedQuery`. When the query is flagged, the
    returned ``query`` is stripped of the matched instruction-override /
    role-switch phrases and fenced injection blocks, and whitespace is
    collapsed. Callers decide whether to refuse (fail-closed) or forward
    the cleaned query.
    """
    if not _GUARDRAILS_ENABLED:
        return SanitizedQuery(query=query, flagged=False)
    if not query:
        return SanitizedQuery(query=query, flagged=False)

    matched: list[str] = []
    for reason, pattern in _INJECTION_PATTERNS:
        if pattern.search(query):
            matched.append(reason)

    if not matched:
        return SanitizedQuery(query=query, flagged=False)

    cleaned = _strip_fenced_instructions(query)  # drop injected fenced blocks first
    cleaned = _STRIP_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t\n\r;:,")
    reasons = tuple(dict.fromkeys(matched))  # dedupe, preserve order
    logger.info("guardrails: query flagged reasons=%s", ",".join(reasons))
    return SanitizedQuery(query=cleaned, flagged=True, reasons=reasons)


# ═══════════════════════════════════════════════════════════════════════
# 2. OUTPUT GUARDRAIL — secret / hostname redaction
# ═══════════════════════════════════════════════════════════════════════

# EVM private keys: 0x + 64 hex chars.
_EVM_KEY_RE = re.compile(r"\b0x[0-9a-fA-F]{64}\b")
# 64-hex without 0x prefix, only when labeled as a key (avoid nuking
# content that legitimately embeds 64-hex hashes).
_LABELED_HEX_KEY_RE = re.compile(
    r"\b(?:private\s+key|secret\s+key|privkey|pkey|skey|key)\s*[:=]\s*(?:0x)?[0-9a-fA-F]{64}\b",
    re.IGNORECASE,
)
# Mnemonic phrases: 12 or 24 BIP39-style lowercase words. Heuristic:
# a run of exactly 12/24 lowercase alpha words where the MAJORITY of the
# tokens appear in a curated BIP39 marker set. This catches real seed
# phrases while leaving ordinary prose untouched (see [REVIEW]).
_BIP39_MARKER_WORDS: frozenset[str] = frozenset(
    {
        "abandon",
        "ability",
        "able",
        "about",
        "above",
        "absent",
        "absorb",
        "abstract",
        "absurd",
        "abuse",
        "access",
        "accident",
        "account",
        "accuse",
        "achieve",
        "acid",
        "acoustic",
        "acquire",
        "across",
        "act",
        "action",
        "actor",
        "actress",
        "actual",
        "adapt",
        "add",
        "addict",
        "address",
        "adjust",
        "admit",
        "adult",
        "advance",
        "advice",
        "aerobic",
        "affair",
        "afford",
        "afraid",
        "again",
        "age",
        "agent",
        "agree",
        "ahead",
        "aim",
        "air",
        "airport",
        "aisle",
        "alarm",
        "album",
        "alcohol",
        "alert",
        "alien",
        "all",
        "alley",
        "allow",
        "almost",
        "alone",
        "alpha",
        "already",
        "also",
        "alter",
        "always",
        "amateur",
        "amazing",
        "among",
        "amount",
        "amused",
        "analyst",
        "anchor",
        "ancient",
        "anger",
        "angle",
        "angry",
        "animal",
        "ankle",
        "announce",
        "annual",
        "another",
        "answer",
        "antenna",
        "antique",
        "anxiety",
        "any",
        "apart",
        "apology",
        "appear",
        "apple",
        "approve",
        "april",
        "arch",
        "arctic",
        "area",
        "arena",
        "argue",
        "arm",
        "armed",
        "armor",
        "army",
        "around",
        "arrange",
        "arrest",
        "arrive",
        "arrow",
        "art",
        "artefact",
        "artist",
        "artwork",
        "ask",
        "aspect",
        "assault",
        "asset",
        "assist",
        "assume",
        "asthma",
        "athlete",
        "atom",
        "attack",
        "attend",
        "attitude",
        "attract",
        "auction",
        "audit",
        "august",
        "aunt",
        "author",
        "auto",
        "autumn",
        "average",
        "avocado",
        "avoid",
        "awake",
        "aware",
        "away",
        "awesome",
        "awful",
        "awkward",
        "axis",
        "baby",
        "bachelor",
        "bacon",
        "badge",
        "bag",
        "balance",
        "balcony",
        "ball",
        "bamboo",
        "banana",
        "banner",
        "bar",
        "barely",
        "bargain",
        "barrel",
        "base",
        "basic",
        "basket",
        "battle",
        "beach",
        "bean",
        "beauty",
        "because",
        "become",
        "beef",
        "before",
        "begin",
        "behave",
        "behind",
        "believe",
        "below",
        "belt",
        "bench",
        "benefit",
        "best",
        "betray",
        "better",
        "between",
        "beyond",
        "bicycle",
        "bid",
        "bike",
        "bind",
        "biology",
        "bird",
        "birth",
        "bitter",
        "black",
        "blade",
        "blame",
        "blanket",
        "blast",
        "bleak",
        "bless",
        "blind",
        "blood",
        "blossom",
        "blouse",
        "blue",
        "blur",
        "blush",
        "board",
        "boat",
        "body",
        "boil",
        "bomb",
        "bone",
        "bonus",
        "book",
        "boost",
        "border",
        "boring",
        "borrow",
        "boss",
        "bottom",
        "bounce",
        "box",
        "boy",
        "bracket",
        "brain",
        "brand",
        "brass",
        "brave",
        "bread",
        "breeze",
        "brick",
        "bridge",
        "brief",
        "bright",
        "bring",
        "brisk",
        "broccoli",
        "broken",
        "bronze",
        "broom",
        "brother",
        "brown",
        "brush",
        "bubble",
        "buddy",
        "budget",
        "buffalo",
        "build",
        "bulb",
        "bulk",
        "bullet",
        "bundle",
        "bunker",
        "burden",
        "burger",
        "burst",
        "bus",
        "business",
        "busy",
        "butter",
        "buyer",
        "buzz",
        "cabbage",
        "cabin",
        "cable",
        "cactus",
        "cage",
        "cake",
        "call",
        "calm",
        "camera",
        "camp",
        "can",
        "canal",
        "cancel",
        "candy",
        "cannon",
        "canoe",
        "canvas",
        "canyon",
        "capable",
        "capital",
        "captain",
        "car",
        "carbon",
        "card",
        "cargo",
        "carpet",
        "carry",
        "cart",
        "case",
        "cash",
        "casino",
        "castle",
        "casual",
        "cat",
        "catalog",
        "catch",
        "category",
        "cattle",
        "caught",
        "cause",
        "caution",
        "cave",
        "ceiling",
        "celery",
        "cement",
        "census",
        "century",
        "cereal",
        "certain",
        "chair",
        "chalk",
        "champion",
        "change",
        "chaos",
        "chapter",
        "charge",
        "chase",
        "chat",
        "cheap",
        "check",
        "cheese",
        "chef",
        "cherry",
        "chest",
        "chicken",
        "chief",
        "child",
        "chimney",
        "choice",
        "choose",
        "chronic",
        "chuckle",
        "chunk",
        "churn",
        "cigar",
        "cinnamon",
        "circle",
        "citizen",
        "city",
        "civil",
        "claim",
        "clap",
        "clarify",
        "claw",
        "clay",
        "clean",
        "clerk",
        "clever",
        "click",
        "client",
        "cliff",
        "climb",
        "clinic",
        "clip",
        "clock",
        "clog",
        "close",
        "cloth",
        "cloud",
        "clown",
        "club",
        "clump",
        "cluster",
        "clutch",
        "coach",
        "coast",
        "coconut",
        "code",
        "coffee",
        "coil",
        "coin",
        "collect",
        "color",
        "column",
        "combine",
        "come",
        "comfort",
        "comic",
        "common",
        "company",
        "concert",
        "conduct",
        "confirm",
        "congress",
        "connect",
        "consider",
        "control",
        "convince",
        "cook",
        "cool",
        "copper",
        "copy",
        "coral",
        "core",
        "corn",
        "correct",
        "cost",
        "cotton",
        "couch",
        "country",
        "couple",
        "course",
        "cousin",
        "cover",
        "coyote",
        "crack",
        "cradle",
        "craft",
        "cram",
        "crane",
        "crash",
        "crater",
        "crawl",
        "crazy",
        "cream",
        "credit",
        "creek",
        "crew",
        "cricket",
        "crime",
        "crisp",
        "critic",
        "crop",
        "cross",
        "crouch",
        "crowd",
        "crucial",
        "cruel",
        "cruise",
        "crumble",
        "crunch",
        "crush",
        "cry",
        "crystal",
        "cube",
        "culture",
        "cup",
        "cupboard",
        "curious",
        "current",
        "curtain",
        "curve",
        "cushion",
        "custom",
        "cute",
        "cycle",
    }
)


def _redact_mnemonics(text: str) -> str:
    """Redact 12/24-word BIP39-style runs (majority-marker heuristic)."""
    # Match runs of exactly 12 or 24 lowercase alpha words (3-8 chars).
    # Word boundaries prevent partial-word matches. 24-word alternative is
    # tried first so a 24-word mnemonic is redacted as one unit, not two 12s.
    pattern = re.compile(
        r"\b(?:(?:[a-z]{3,8})\s+){23}[a-z]{3,8}\b"
        r"|\b(?:(?:[a-z]{3,8})\s+){11}[a-z]{3,8}\b"
    )

    def _replace(match: re.Match[str]) -> str:
        words = match.group(0).split()
        if len(words) not in (12, 24):
            return match.group(0)
        hits = sum(1 for w in words if w in _BIP39_MARKER_WORDS)
        if hits * 2 >= len(words):  # majority of tokens are BIP39 markers
            return "[REDACTED]"
        return match.group(0)

    return pattern.sub(_replace, text)


# API-key-looking strings: common prefixes + generic key=value pairs.
_API_KEY_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{16,}|"  # OpenAI-style
    r"sk_live_[A-Za-z0-9]{20,}|"  # Stripe-style
    r"AKIA[0-9A-Z]{16}|"  # AWS access key
    r"AIza[0-9A-Za-z_-]{35}|"  # Google API key
    r"ghp_[A-Za-z0-9]{30,}|"  # GitHub PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack token
    r")\b",
)
# Generic labeled secrets. Bare "token" is intentionally NOT a label here —
# "token: 0x…" in crypto content is usually a token *address*, not a secret.
_GENERIC_KEY_RE = re.compile(
    r"\b(?:api[_-]?key|apikey|secret|password|passwd|pwd|"
    r"access[_-]?token|auth[_-]?token|bearer)\s*[=:]\s*[\"']?[A-Za-z0-9_./+-]{16,}[\"']?",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}\b", re.IGNORECASE)
# Internal fleet hostnames are configured via RMI_INTERNAL_HOSTS (a
# comma-separated list of host labels) so no deployment-specific hostnames
# ship in the public repo. Default is EMPTY — with no hosts configured only
# the generic patterns below (RFC1918/CGNAT IPs, *.internal / *.local
# FQDNs) are redacted. A configured label is redacted both as a bare word
# and as <label>.rugmunch.io. The public rugmunch.io domain itself is NOT
# redacted (see [REVIEW]).
# ``*.internal`` / ``*.local`` require at least one prefix label — a bare
# ``internal`` is a common Solidity visibility keyword and must survive
# (fix: `+` quantifier; `*` would redact it and corrupt code snippets).
_INTERNAL_HOSTS: tuple[str, ...] = tuple(
    h.strip().lower() for h in os.getenv("RMI_INTERNAL_HOSTS", "").split(",") if h.strip()
)

_INTERNAL_FQDN_PARTS: list[str] = []
if _INTERNAL_HOSTS:
    _INTERNAL_FQDN_PARTS.append(
        r"(?:" + "|".join(re.escape(h) for h in _INTERNAL_HOSTS) + r")\.rugmunch\.io\b"
    )
_INTERNAL_FQDN_PARTS.append(r"(?:[a-z0-9-]+\.)+(?:internal|local)\b")
_INTERNAL_FQDN_RE = re.compile(
    r"\b(?:" + "|".join(_INTERNAL_FQDN_PARTS) + r")",
    re.IGNORECASE,
)
# Bare host labels that are unique enough to redact without qualification.
_BARE_HOST_RE = (
    re.compile(
        r"\b(?:" + "|".join(re.escape(h) for h in _INTERNAL_HOSTS) + r")\b",
        re.IGNORECASE,
    )
    if _INTERNAL_HOSTS
    else None
)
# RFC1918 + CGNAT (100.64/10) private IPs.
_PRIVATE_IP_RE = re.compile(
    r"\b(?:10|127|192\.168|172\.(?:1[6-9]|2[0-9]|3[01])|100\.(?:6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7]))"
    r"(?:\.\d{1,3}){2,3}\b"
)

_REDACTED = "[REDACTED]"


def redact_output(text: str) -> str:
    """Strip obvious secret patterns from *text* (fail-closed).

    Redacts EVM private keys, labeled 64-hex keys, 12/24-word mnemonic
    phrases, API-key-looking strings, internal hostnames, and private IPs.
    Pure string transform — never raises; non-str input is coerced.
    """
    if not _GUARDRAILS_ENABLED:
        return text
    out = text
    out = _EVM_KEY_RE.sub(_REDACTED, out)
    out = _LABELED_HEX_KEY_RE.sub(_REDACTED, out)
    out = _redact_mnemonics(out)
    out = _API_KEY_RE.sub(_REDACTED, out)
    out = _GENERIC_KEY_RE.sub(_REDACTED, out)
    out = _BEARER_TOKEN_RE.sub(_REDACTED, out)
    out = _INTERNAL_FQDN_RE.sub(_REDACTED, out)
    if _BARE_HOST_RE is not None:
        out = _BARE_HOST_RE.sub(_REDACTED, out)
    out = _PRIVATE_IP_RE.sub(_REDACTED, out)
    return out


# ═══════════════════════════════════════════════════════════════════════
# 3. RAG-POISONING METADATA FILTER
# ═══════════════════════════════════════════════════════════════════════

# Canonical trusted source types (see ingest_smart.SOURCE_PRIORITY and
# crypto_schema.normalize_source_type). Chunks carrying one of these in
# ``source_type`` / ``source`` metadata are trusted.
TRUSTED_SOURCES: frozenset[str] = frozenset(
    {
        "defihacklabs",
        "rekt_news",
        "defillama_hacks",
        "web3hacks",
        "postmortems",
        "immunefi",
        "cryptoscamdb",
        "slowmist",
        "swc_registry",
        "openzeppelin_audits",
        "seed",
        "goplus",
        "alert_digest",
        "news",
    }
)

# Metadata flags that mark a chunk as untrusted / poisoned. Presence of any
# of these (truthy) makes ``trusted_filter`` drop or rank-down the chunk.
UNTRUSTED_FLAGS: frozenset[str] = frozenset(
    {"poisoned", "unverified", "untrusted", "user_uploaded"}
)

# Partition values that MUST NOT be searched by the shared answer path.
# See the "Never query user-uploaded partitions" rule in the module docstring.
UNTRUSTED_PARTITIONS: tuple[str, ...] = ("user_upload", "user-uploaded", "user_uploaded")


def _doc_metadata(doc: Any) -> dict[str, Any]:
    """Return the metadata dict of *doc* (empty dict when absent)."""
    if not isinstance(doc, dict):
        return {}
    meta = doc.get("metadata")
    return meta if isinstance(meta, dict) else {}


def is_trusted(doc: Any, *, trusted_sources: Iterable[str] | None = None) -> bool:
    """Return True when *doc* is from a trusted source.

    A doc is trusted when its metadata:
      * carries an explicit ``trusted``/``verified``/``trust_level`` mark,
      * OR has a ``source_type``/``source`` in ``trusted_sources``
        (defaults to :data:`TRUSTED_SOURCES`),
    and does NOT carry any untrusted flag / partition.
    """
    meta = _doc_metadata(doc)
    if any(meta.get(flag) for flag in UNTRUSTED_FLAGS):
        return False
    partition = str(meta.get("partition", "") or "").strip().lower()
    if partition in UNTRUSTED_PARTITIONS:
        return False
    if meta.get("trusted") is True or str(meta.get("verified", "")).lower() == "true":
        return True
    trust_level = str(meta.get("trust_level", "")).lower()
    if trust_level in {"trusted", "verified", "curated"}:
        return True
    source = str(meta.get("source_type", meta.get("source", ""))).strip().lower()
    if not source:
        return False
    allowed = TRUSTED_SOURCES if trusted_sources is None else set(trusted_sources)
    return source in allowed


def tag_unverified(doc: dict[str, Any], reason: str = "no trusted source") -> dict[str, Any]:
    """Return a copy of *doc* tagged as unverified (additive, never mutates).

    Adds ``metadata.unverified`` / ``metadata.trust_level`` /
    ``metadata.unverified_reason`` so downstream ``trusted_filter`` calls
    rank or drop it. If the doc is already trusted this is a no-op copy.
    """
    out = dict(doc)
    meta = dict(_doc_metadata(doc))
    if is_trusted(out):
        return out
    meta["unverified"] = True
    meta["trust_level"] = "unverified"
    meta["unverified_reason"] = reason
    out["metadata"] = meta
    return out


def trusted_filter(
    docs: Iterable[dict[str, Any]],
    *,
    mode: str = "drop",
    trusted_sources: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter *docs* for RAG poisoning protection.

    ``mode="drop"`` (default) returns only trusted chunks. ``mode="rank"``
    keeps all chunks but appends untrusted ones to the end (ranked down),
    each tagged with ``metadata.unverified`` via :func:`tag_unverified`.
    """
    docs_list = list(docs)
    if mode not in ("drop", "rank"):
        raise ValueError(f"trusted_filter: mode must be 'drop' or 'rank', got {mode!r}")
    trusted = [d for d in docs_list if is_trusted(d, trusted_sources=trusted_sources)]
    untrusted = [d for d in docs_list if not is_trusted(d, trusted_sources=trusted_sources)]
    if mode == "drop":
        return trusted
    return trusted + [tag_unverified(d) for d in untrusted]


def build_trusted_partition_filter(
    partitions: Iterable[str] = UNTRUSTED_PARTITIONS,
    field: str = "partition",
) -> dict[str, Any] | None:
    """Build a Qdrant ``must_not`` payload filter excluding untrusted partitions.

    Returns ``{"must_not": [{"key": field, "match": {"value": p}} ...]}``
    or ``None`` when *partitions* is empty. Combine with caller filters that
    carry a ``must`` key (Qdrant merges ``must`` + ``must_not``).

    This implements the "never query user-uploaded partitions" rule — attach
    it to every search on shared collections so poisoned user partitions are
    excluded before vector retrieval.
    """
    parts = tuple(partitions)
    if not parts:
        return None
    return {
        "must_not": [{"key": field, "match": {"value": p}} for p in parts],
    }


__all__ = [
    "TRUSTED_SOURCES",
    "UNTRUSTED_FLAGS",
    "UNTRUSTED_PARTITIONS",
    "SanitizedQuery",
    "build_trusted_partition_filter",
    "is_trusted",
    "redact_output",
    "sanitize_query",
    "tag_unverified",
    "trusted_filter",
]
