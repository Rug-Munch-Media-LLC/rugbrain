"""RAG-poisoning guardrail — trusted-source classification."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

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
