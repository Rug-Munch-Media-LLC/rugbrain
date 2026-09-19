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

from rugbrain.guardrails import _wordlist as _wordlist
from rugbrain.guardrails._config import (
    _GUARDRAILS_ENABLED as _GUARDRAILS_ENABLED,
)
from rugbrain.guardrails._config import (
    logger as logger,
)
from rugbrain.guardrails.partition import (
    build_trusted_partition_filter as build_trusted_partition_filter,
)
from rugbrain.guardrails.redact import (
    _API_KEY_RE as _API_KEY_RE,
)
from rugbrain.guardrails.redact import (
    _BARE_HOST_RE as _BARE_HOST_RE,
)
from rugbrain.guardrails.redact import (
    _BEARER_TOKEN_RE as _BEARER_TOKEN_RE,
)
from rugbrain.guardrails.redact import (
    _EVM_KEY_RE as _EVM_KEY_RE,
)
from rugbrain.guardrails.redact import (
    _GENERIC_KEY_RE as _GENERIC_KEY_RE,
)
from rugbrain.guardrails.redact import (
    _INTERNAL_FQDN_PARTS as _INTERNAL_FQDN_PARTS,
)
from rugbrain.guardrails.redact import (
    _INTERNAL_FQDN_RE as _INTERNAL_FQDN_RE,
)
from rugbrain.guardrails.redact import (
    _INTERNAL_HOSTS as _INTERNAL_HOSTS,
)
from rugbrain.guardrails.redact import (
    _LABELED_HEX_KEY_RE as _LABELED_HEX_KEY_RE,
)
from rugbrain.guardrails.redact import (
    _PRIVATE_IP_RE as _PRIVATE_IP_RE,
)
from rugbrain.guardrails.redact import (
    _REDACTED as _REDACTED,
)
from rugbrain.guardrails.redact import (
    _redact_mnemonics as _redact_mnemonics,
)
from rugbrain.guardrails.redact import (
    redact_output as redact_output,
)
from rugbrain.guardrails.sanitize import (
    _FENCE_RE as _FENCE_RE,
)
from rugbrain.guardrails.sanitize import (
    _INJECTION_PATTERNS as _INJECTION_PATTERNS,
)
from rugbrain.guardrails.sanitize import (
    _STRIP_RE as _STRIP_RE,
)
from rugbrain.guardrails.sanitize import (
    SanitizedQuery as SanitizedQuery,
)
from rugbrain.guardrails.sanitize import (
    _strip_fenced_instructions as _strip_fenced_instructions,
)
from rugbrain.guardrails.sanitize import (
    sanitize_query as sanitize_query,
)
from rugbrain.guardrails.tag_unverified import tag_unverified as tag_unverified
from rugbrain.guardrails.trust import (
    TRUSTED_SOURCES as TRUSTED_SOURCES,
)
from rugbrain.guardrails.trust import (
    UNTRUSTED_FLAGS as UNTRUSTED_FLAGS,
)
from rugbrain.guardrails.trust import (
    UNTRUSTED_PARTITIONS as UNTRUSTED_PARTITIONS,
)
from rugbrain.guardrails.trust import (
    _doc_metadata as _doc_metadata,
)
from rugbrain.guardrails.trust import (
    is_trusted as is_trusted,
)
from rugbrain.guardrails.trusted_filter import trusted_filter as trusted_filter

_BIP39_MARKER_WORDS: frozenset[str] = _wordlist.BIP39_MARKER_WORDS

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
