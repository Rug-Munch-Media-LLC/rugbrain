"""Embed input derivation, and the DeepInfra 8B/4096 embed call.

TEXT DERIVATION is the part most likely to quietly produce a twin nobody can
trust, so it is explicit rather than clever, and every derivation is PROVABLE
before it is embedded.

OPERATOR DECISION (2026-09-29, revised): a previous agent measured which
collections can be re-embedded from their own payload, and that measurement
SHRANK the job from 60,255 points to ~8k. Two of those findings are enforced
here as hard rules:

  1. NO SYNTHETIC PROSE. ``scan_intel`` (52k) and ``agent_vectors`` (182)
     carry no text field at all. Their vectors were built from text that is
     not recoverable from the payload, so inventing ``chain=...; address=...``
     filler and calling it an embed would write 52k vectors that match nothing
     a user can find. The derivation below therefore has NO reconstruction
     path: those payloads yield ``("", "unrecoverable")`` and are counted and
     reported, never embedded. Another lane is moving them to ClickHouse as
     address-keyed lookup data, where the keys are the point.
  2. DERIVED TEXT MUST PROVE ITSELF. ``codebase_reference_v2`` (4,644) has
     no ``content`` field either, but its text is recoverable and CHECKABLE:
     the payload stores ``content_hash``, which the ingest writer
     (``cc_ingest/writer.py``) defines as the sha1 of the exact embed text.
     So the derived text is hashed and COMPARED. Verified 2026-09-29 on the
     4,644 points actually missing from the twin: 401/401 sampled reproduce
     byte-exactly under ``writer.py::_embed_text``. Anything that fails the
     oracle is REFUSED, not embedded on a guess — see ``derive_text``.

Rule 5 of the brief — a returned batch is not silently accepted at the wrong
size — is enforced in ``embed_batch``: width AND count are both checked.
"""

from __future__ import annotations

import hashlib
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

# Priority order. First non-blank string wins. Every one of these was confirmed
# present on the live collections it is relied on (2026-09-29 payload scan).
TEXT_FIELDS = ("content", "text", "document", "chunk", "embedding_input", "body")

# The SECOND derivation, for payloads with no text field but a provable one.
# codebase_reference_v2 stores the embed input's sha1 in ``content_hash``
# (cc_ingest/writer.py::content_hash), so a reconstruction that hashes equal is
# not a guess — it is the original text.
#
# The rule is cc_ingest/writer.py::_embed_text, kept EXACTLY:
#     f"{library_id} {file_path}:{function_name or title}"
#     "\n\n{title}\n\n{description}\n\n{code_block}"
# The "qualifier FIRST" ordering is not cosmetic: a bare `get` chunk from four
# libraries otherwise embeds near-identical.
#
# MEASURED 2026-09-29 against the content_hash oracle, on the two populations:
#   * the 4,644 points actually missing from the twin (the real work):
#     401/401 = 100.0% reproduce byte-exactly under this rule;
#   * a 298-point sample from the HEAD of the base: 272/298 = 91.3%.
# The head is a different builder (docs:-prefixed libraries whose file_path is
# a URL) and is the minority; the tail is the work, and the rule is exact
# there. A "drop the blank fields" simplification scored 91.3% on the head and
# 0.0% on the tail — a sampling artefact that would have silently refused the
# entire collection. The oracle is what settles it, not a sample.
NATIVE_MAX_CHARS = 10_000


def deepinfra_key() -> str:
    """Env first, secrets file fallback. Never logged, never printed."""
    k = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if k:
        return k
    p = Path(DEEPINFRA_KEY_FILE)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _s(payload: dict[str, Any], field: str) -> str:
    v = payload.get(field)
    return v if isinstance(v, str) else ""


def qualifier(payload: dict[str, Any]) -> str:
    """``library_id file_path:function_name or title`` — the ingest writer's."""
    return (
        f"{_s(payload, 'library_id')} {_s(payload, 'file_path')}"
        f":{_s(payload, 'function_name') or _s(payload, 'title')}"
    )


def hash_of_text(text: str) -> str:
    """sha1 because that is what the INGEST side writes.

    Not a security decision: ``content_hash`` is an incremental-refresh
    identity set by cc_ingest/writer.py, and this only compares against it.
    The value is never used to authenticate anything, which is why sha1 is
    correct here and a wider hash would simply stop matching.
    """
    return hashlib.sha1(text.encode("utf-8")).hexdigest()  # noqa: S324


def derive_text(payload: dict[str, Any]) -> str:
    """Rebuild the embed input for a payload that has no text field.

    Kept separate from ``embed_text`` so the caller can PROVE it against
    ``content_hash`` before spending a DeepInfra call.
    """
    parts = [
        qualifier(payload),
        _s(payload, "title"),
        _s(payload, "description"),
        _s(payload, "code_block"),
    ]
    return "\n\n".join(parts).strip()[:NATIVE_MAX_CHARS]


def embed_text(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (text, source). Never invents prose.

    source is one of:
      native         — the payload carries its own text field; used verbatim.
      derived        — rebuilt AND sha1-confirmed against ``content_hash``.
      unrecoverable  — no text and no provable reconstruction (scan_intel,
                       agent_vectors). Returns "" so the caller skips it.

    Does not mutate the caller's payload.
    """
    for f in TEXT_FIELDS:
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            return v[:NATIVE_MAX_CHARS], "native"

    expected = payload.get("content_hash")
    if isinstance(expected, str) and expected:
        candidate = derive_text(payload)
        if candidate and hash_of_text(candidate) == expected:
            return candidate, "derived"
        # Mismatch is a different builder (the docs: libraries). Refusing here
        # is the point: a near-miss vector is unfindable and costs DeepInfra
        # twice — once to embed, once to discover it is noise.
        return "", "unrecoverable"
    return "", "unrecoverable"


def embed_batch(h: httpx.Client, texts: list[str], key: str) -> list[list[float]]:
    """One HTTP call for a whole batch, response order restored.

    BOTH contracts are CHECKED, never trusted, because both have a precedent
    here:

      * the WIDTH — a 2560-dim answer to a 4096 collection is the exact
        failure this project has been bitten by twice (the silent 4B/2560
        write, then the store's dimension guard);
      * the COUNT — the ``rerank_chunks`` defect class, where a keep/top_n
        default turned a 40-document pool into 5 results and the length guard
        swallowed the shortfall as a no-op. A short batch here would silently
        drop points from the tail of a page and the twin would under-fill by
        exactly the number that vanished, so it raises instead. The
        alternative — zipping what came back — is the bug, not the fix.
    """
    if not texts:
        return []
    r = h.post(
        f"{DEEPINFRA_URL}/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": DEEPINFRA_MODEL, "input": texts},
    )
    if r.status_code != 200:
        raise Abort(f"DeepInfra HTTP {r.status_code}: {r.text[:200]}")
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    if len(data) != len(texts):
        raise Abort(
            f"DeepInfra returned {len(data)} vectors for {len(texts)} inputs; "
            "refusing to write a partial batch (this is the rerank_chunks defect class)"
        )
    out: list[list[float]] = []
    for d in data:
        emb = d["embedding"]
        if len(emb) != CANONICAL_DIM:
            raise Abort(f"expected {CANONICAL_DIM}-dim from {DEEPINFRA_MODEL}, got {len(emb)}")
        out.append(emb)
    return out
