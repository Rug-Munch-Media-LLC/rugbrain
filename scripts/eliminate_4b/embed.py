"""Embed input derivation, and the DeepInfra 8B/4096 embed call.

TEXT DERIVATION is the part most likely to quietly produce a twin nobody can
trust, so it is explicit rather than clever.

Measured 2026-09-29: where a payload carries a text field, that field IS the
embed input the existing twins were built from — security_feeds base and twin
``content`` are byte-identical on paired ids, and codebase_reference_v2's
paired points share the same body fields. So native text is used verbatim.

``scan_intel`` is the exception and the bulk of the work (52k points): a
40-point sample showed it carries NO text field at all — only kind, doc_id,
address, chain, symbol, name, risk_score, safety_score, risk_level,
risk_flags, scanned_at. The text the 2560 vectors were built from is not
recoverable from the payload. Rather than invent prose, the embed input is
rebuilt deterministically from the fields that ARE present and the point is
stamped ``text_source: "reconstructed"`` so it stays auditable and separable
from natively-texted points. [REVIEW] the operator may prefer to EXCLUDE
scan_intel: 52k points, none of which had recoverable text, at ~$0.02/M.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from .qdrant_io import Abort

logger = logging.getLogger("eliminate_4b.embed")

DEEPINFRA_URL = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1").rstrip("/")
DEEPINFRA_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
DEEPINFRA_KEY_FILE = os.getenv("DEEPINFRA_KEY_FILE", "/srv/secrets/deepinfra_api_key")
CANONICAL_DIM = 4096

# Priority order. First non-blank string wins.
TEXT_FIELDS = ("content", "text", "document", "chunk", "embedding_input", "body")
# The fields scan_intel-style payloads do carry, in reading order.
RECONSTRUCT_KEYS = (
    "kind",
    "chain",
    "address",
    "symbol",
    "name",
    "risk_level",
    "risk_score",
    "safety_score",
    "risk_flags",
)
RECON_MAX_CHARS = 2000
NATIVE_MAX_CHARS = 10_000


def deepinfra_key() -> str:
    """Env first, secrets file fallback. Never logged, never printed."""
    k = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if k:
        return k
    p = Path(DEEPINFRA_KEY_FILE)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def embed_text(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (text, source) with source in {native, reconstructed, empty}.

    Does not mutate the caller's payload.
    """
    for f in TEXT_FIELDS:
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            return v[:NATIVE_MAX_CHARS], "native"
    parts = [
        f"{k}={payload[k]}" for k in RECONSTRUCT_KEYS if payload.get(k) not in (None, "", [], {})
    ]
    if not parts:
        return "", "empty"
    return "; ".join(parts)[:RECON_MAX_CHARS], "reconstructed"


def embed_batch(h: httpx.Client, texts: list[str], key: str) -> list[list[float]]:
    """One HTTP call for a whole batch, response order restored.

    The width is CHECKED, never trusted. A 2560-dim answer to a 4096
    collection is the exact failure this project has been bitten by twice
    (the silent 4B/2560 write, then the store's dimension guard), so it
    aborts here instead of writing a wrong-width vector.
    """
    r = h.post(
        f"{DEEPINFRA_URL}/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": DEEPINFRA_MODEL, "input": texts},
    )
    if r.status_code != 200:
        raise Abort(f"DeepInfra HTTP {r.status_code}: {r.text[:200]}")
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    if len(data) != len(texts):
        raise Abort(f"DeepInfra returned {len(data)} vectors for {len(texts)} inputs")
    out: list[list[float]] = []
    for d in data:
        emb = d["embedding"]
        if len(emb) != CANONICAL_DIM:
            raise Abort(f"expected {CANONICAL_DIM}-dim from {DEEPINFRA_MODEL}, got {len(emb)}")
        out.append(emb)
    return out
