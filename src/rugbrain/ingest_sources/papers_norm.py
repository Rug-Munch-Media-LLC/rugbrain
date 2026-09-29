"""Deterministic normalization for the papers/standards ingest.

Split out of :mod:`rugbrain.ingest_sources.papers` to keep each module
under the 200-line size ratchet. This module is **pure**: it holds the
collection/lane declaration, the deterministic point-id derivation and the
two normalizers (EIP markdown, DeFiLlama protocol row). No network, no
I/O, no Qdrant — which is exactly why it is the part worth unit-testing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# --- Collection + lane declaration -------------------------------------
#: ONE collection for both sources. Deliberately NOT ``research_v2`` (that
#: holds 158 RSS rows we must not touch) and NOT the existing-but-empty
#: ``standards`` (a collection nobody in this lane owns).
COLLECTION = "papers_standards"
#: Canonical lane: Qwen3-Embedding-8B, 4096-dim.
EMBEDDING_LANE = "qwen3-embedding-8b"
VECTOR_SIZE = 4096
DISTANCE = "Cosine"

EIPS_REPO = "ethereum/EIPs"
EIPS_REF = "master"
EIPS_LICENSE = "CC0-1.0"
DEFILLAMA_URL = "https://api.llama.fi/protocols"
DEFILLAMA_LICENSE = "no licence declared by upstream"

EIP_PATH_RE = re.compile(r"^EIPS/eip-(\d+)\.md$")
MOVED_MARKER = "This file was moved to"
#: EIP files below this size are front-matter-only redirects, not specs.
STUB_BYTES = 400


@dataclass(frozen=True)
class SourceDoc:
    """One harvested document, pre-embedding.

    ``point_id`` is deterministic: the same upstream document always maps
    to the same id, which is what makes a re-run an upsert rather than a
    duplicate.
    """

    point_id: int
    title: str
    text: str
    source: str
    license: str
    url: str
    metadata: dict[str, Any] = field(default_factory=dict)


def point_id(source: str, key: str) -> int:
    """Deterministic Qdrant point id for one upstream document.

    Stable across runs — no timestamp, no random suffix — so re-running the
    ingest upserts the same points. A non-negative 63-bit int derived from
    SHA-256, matching Qdrant's unsigned integer-id requirement.
    """
    digest = hashlib.sha256(f"{COLLECTION}:{source}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def frontmatter(text: str) -> dict[str, str]:
    """Parse the leading ``---`` YAML block into flat key/value pairs."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        key, _, val = line.partition(":")
        out[key.strip()] = val.strip().strip('"')
    return out


def is_stub(text: str) -> bool:
    """True when an EIP file is a moved-redirect, not a specification."""
    return MOVED_MARKER in text or len(text) < STUB_BYTES


def parse_eip(path: str, text: str, url: str) -> SourceDoc | None:
    """Normalize one EIP file into a ``SourceDoc``; ``None`` when a stub."""
    m = EIP_PATH_RE.match(path)
    if not m or is_stub(text):
        return None
    number = m.group(1)
    meta = frontmatter(text)
    return SourceDoc(
        point_id=point_id("eips", number),
        title=meta.get("title") or f"EIP-{number}",
        text=text.strip(),
        source="eips",
        license=EIPS_LICENSE,
        url=url,
        metadata={
            "eip": int(number),
            "status": meta.get("status", ""),
            "type": meta.get("type", ""),
            "category": meta.get("category", ""),
            "created": meta.get("created", ""),
            "collection": COLLECTION,
            "embedding_lane": EMBEDDING_LANE,
            "vector_size": VECTOR_SIZE,
            "license": EIPS_LICENSE,
        },
    )


def protocol_doc(row: dict[str, Any]) -> SourceDoc | None:
    """Normalize one DeFiLlama protocol row into a ``SourceDoc``."""
    slug = str(row.get("slug") or row.get("name") or "").strip()
    if not slug:
        return None
    chains = row.get("chains") or []
    if isinstance(chains, str):
        chains = [chains]
    text = "\n".join(
        [
            f"# {row.get('name', slug)}",
            f"Category: {row.get('category', '')}",
            f"Chains: {', '.join(map(str, chains))}",
            f"Address: {row.get('address') or ''}",
            f"URL: {row.get('url') or ''}",
            str(row.get("description") or ""),
        ]
    ).strip()
    return SourceDoc(
        point_id=point_id("defillama", slug),
        title=str(row.get("name") or slug),
        text=text,
        source="defillama",
        license=DEFILLAMA_LICENSE,
        url=str(row.get("url") or f"https://defillama.com/protocol/{slug}"),
        metadata={
            "slug": slug,
            "category": row.get("category", ""),
            "address": row.get("address") or "",
            "chain": row.get("chain", ""),
            "chains": chains,
            "tvl": row.get("tvl") or 0,
            "collection": COLLECTION,
            "embedding_lane": EMBEDDING_LANE,
            "vector_size": VECTOR_SIZE,
            "license": DEFILLAMA_LICENSE,
        },
    )


__all__ = [
    "COLLECTION",
    "DEFILLAMA_LICENSE",
    "DEFILLAMA_URL",
    "DISTANCE",
    "EIPS_LICENSE",
    "EIPS_REF",
    "EIPS_REPO",
    "EIP_PATH_RE",
    "EMBEDDING_LANE",
    "STUB_BYTES",
    "VECTOR_SIZE",
    "SourceDoc",
    "frontmatter",
    "is_stub",
    "parse_eip",
    "point_id",
    "protocol_doc",
]
