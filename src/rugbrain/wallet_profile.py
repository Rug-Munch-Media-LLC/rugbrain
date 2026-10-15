"""Wallet profile — a real answer for ``rugbrain_wallet_profile``.

THE DEFECT THIS CLOSES
    ``rugbrain_wallet_profile`` routed to ``compat.investigate``, which returned
    ``{"findings": [], "risk_score": 0.5}`` for every address. An agent calling
    it got a plausible-looking response containing no information — the same
    class of defect as the old ``score_deployer_risk`` stub, and worse than a
    503 because it is indistinguishable from a real "nothing found".

    The data was never missing. Three collections this repo already owns and
    already serves hold per-address labels:

      * ``wallet_profiles_8b`` (40,355 pts) — Etherscan combined labels.
      * ``wallet_v2_8b`` (10,278 pts) — Etherscan malicious labels.
      * ``known_scams_8b`` (26,808 pts) — scam/OFAC/phishing intel.

    The capability was a wiring gap, not a data gap. This module is the wiring.

WHAT IT DOES
    ``profile_wallet(address)`` looks the address up by EXACT payload match
    (``metadata.address``) across the label collections, then derives a verdict
    from the labels it actually found. It does not embed, does not fetch, and
    does not invent: an address with no label returns ``found=False`` and
    ``risk_level="unknown"`` — "we have never seen this address" is a different
    claim from "this address is clean", and only the first is supportable.

    Exact-match, not semantic: a wallet address is an identifier, and a
    nearest-neighbour search for an address returns the *most similar* address,
    which is a different wallet. The retrieval-routing rule is explicit —
    exact identifiers never go to semantic search first.

    The verdict rule itself lives in :mod:`rugbrain.wallet_profile_rules`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from rugbrain.wallet_profile_rules import LABEL_COLLECTIONS, build_profile

logger = logging.getLogger(__name__)

#: A 0x-prefixed 40-hex EVM address. Solana base58 mints are also accepted by
#: the caller, but the label corpora are EVM-keyed, so the exact-match filter
#: is only issued for a shape the payload actually carries.
_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

#: An EVM address or a Solana base58 mint (32-44 chars). A bare address is
#: never a natural-language question, so the route uses this to decide whether
#: to answer from the label corpora or fall through to the narrative stub.
_ADDRESS_RE = re.compile(r"^(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def looks_like_address(value: str) -> bool:
    """True when *value* is an address, not a natural-language query."""
    return bool(_ADDRESS_RE.match((value or "").strip()))


async def _exact_hits(address: str) -> list[tuple[str, dict[str, Any]]]:
    """Exact ``metadata.address`` match across the label collections.

    Uses the Qdrant scroll filter (payload index on ``address``), not a vector
    search: an address is an identifier, and a nearest-neighbour lookup would
    return a *different* wallet that merely looks similar.

    The store's ``_get_client()`` is an ``httpx.AsyncClient`` bound to the
    Qdrant base URL (not a ``qdrant_client``), so the scroll is issued as a
    REST POST — the same shape ``rug_events.qdrant_io`` uses.
    """
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore()
    client = await store._get_client()
    out: list[tuple[str, dict[str, Any]]] = []
    for collection in LABEL_COLLECTIONS:
        try:
            response = await client.post(
                f"/collections/{collection}/points/scroll",
                json={
                    "limit": 20,
                    "with_payload": True,
                    "with_vector": False,
                    "filter": {"must": [{"key": "metadata.address", "match": {"value": address}}]},
                },
            )
            if response.status_code != 200:
                logger.debug(
                    "wallet_profile_scroll_status collection=%s code=%d",
                    collection,
                    response.status_code,
                )
                continue
            points = (response.json().get("result") or {}).get("points") or []
        except Exception as exc:  # a missing collection must not abort the profile
            logger.debug("wallet_profile_scroll_failed collection=%s err=%s", collection, exc)
            continue
        for point in points:
            payload = point.get("payload") or {}
            if isinstance(payload, dict):
                out.append((collection, payload))
    return out


async def profile_wallet(address: str, max_hops: int = 2) -> dict[str, Any]:
    """Profile a wallet/contract address from the label corpora.

    ``max_hops`` is accepted for API compatibility with the old
    ``investigate`` signature; the label corpora are flat, so it is recorded
    in the response rather than used to traverse a graph this repo does not
    hold. A future graph-backed implementation can honour it.
    """
    addr = (address or "").strip()
    if not addr:
        return {"error": "address is required"}
    hits = await _exact_hits(addr)
    profile = build_profile(addr, hits)
    profile["max_hops"] = max_hops
    profile["address_shape"] = "evm" if _EVM_RE.match(addr) else "non-evm"
    return profile


__all__ = ["looks_like_address", "profile_wallet"]
