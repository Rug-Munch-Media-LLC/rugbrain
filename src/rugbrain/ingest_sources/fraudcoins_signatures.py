"""FraudCoins holder-concentration + GoPlus contract privileges → scam_signatures.

Source: https://huggingface.co/datasets/Fraudcoins/crypto-holder-concentration
(CC BY 4.0, DOI 10.5281/zenodo.22494421 — verified 2026-09-28). Publishes daily
on-chain measurements of top-10 holder concentration plus the GoPlus Labs
``token_security`` owner-privilege / structural flags per token.

This is the closest public analogue to the ``scam_signatures`` backbone
RugBrain lacks: it supplies ``top10_pct`` (private-wallet corrected) and the
privilege booleans ``isMintable`` / ``isBlacklisted`` / ``hiddenOwner`` /
``selfDestruct`` / ``transferPausable`` / ``canTakeBackOwnership`` /
``ownerChangeBalance`` plus the structural ``isProxy`` / ``isOpenSource``.

Emits one ``IngestDoc`` per token into the ``scam_signatures`` collection
(doc_type ``scam_signature``) — STRUCTURED, so the payload is filterable
(Qdrant payload indexes) and computable rather than prose-only.

Design rules (consistent with ``base.py`` / ``deflabx.py``):

  * Fail-open: a missing file or bad JSON logs + continues; all-failed → ``[]``.
  * Deterministic ``doc_id`` (``fraudcoins:<chain>:<address>``) — idempotent.
  * ``null`` upstream means "not reported", NOT "safe": kept as ``None`` and
    never coerced to ``False`` (upstream says so explicitly in its caveat).
  * Credentials: none — raw huggingface.co public resolve URLs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "scam_signatures"
HF_RAW = "https://huggingface.co/datasets/Fraudcoins/crypto-holder-concentration/raw/main"
PERMISSIONS_URL = f"{HF_RAW}/contract-permissions.json"
CONCENTRATION_URL = f"{HF_RAW}/concentration.json"
HISTORY_URL = f"{HF_RAW}/history.json"
DATASET_URL = "https://huggingface.co/datasets/Fraudcoins/crypto-holder-concentration"

# Owner-privilege flags, per the dataset's own ``fieldGroups.ownerPrivileges``.
PRIVILEGE_FIELDS = (
    "isMintable",
    "ownerChangeBalance",
    "transferPausable",
    "isBlacklisted",
    "canTakeBackOwnership",
    "hiddenOwner",
    "selfDestruct",
)
STRUCTURAL_FIELDS = ("isProxy", "isOpenSource")
# Concentration/custody signals from concentration.json + history.json.
RISK_FIELDS = ("top10_pct", "top10_pct_raw", "flagged")


async def _load(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """GET + parse one JSON object; ``{}`` on any failure (fail-open).

    The per-request deadline is the client's own (``Source.timeout``), so no
    timeout parameter is threaded through.
    """
    try:
        r = await client.get(url)
        if r.status_code != 200:
            log.debug("fraudcoins_fetch_non200 url=%s status=%d", url, r.status_code)
            return {}
        data = json.loads(r.text or "{}")
    except Exception as e:
        log.warning("fraudcoins_fetch_failed url=%s err=%s", url, str(e)[:160])
        return {}
    return data if isinstance(data, dict) else {}


def _pct_series(history: dict[str, Any], coin_id: str) -> list[list[Any]]:
    """Pull one coin's ``[[date, pct], ...]`` series out of history.json."""
    coins = history.get("coins") or {}
    series = coins.get(coin_id) if isinstance(coins, dict) else None
    return series if isinstance(series, list) else []


def token_to_doc(
    key: str,
    entry: dict[str, Any],
    concentration: dict[str, Any],
    history: dict[str, Any],
) -> IngestDoc | None:
    """Normalize one token entry into a ``scam_signatures`` IngestDoc.

    ``key`` is the upstream ``<chain>:<address>`` identifier. Returns ``None``
    when the entry carries neither an address nor a coinId, so a malformed row
    never creates a blank point.
    """
    if not isinstance(entry, dict):
        return None
    address = str(entry.get("address") or "").strip()
    chain = str(entry.get("chain") or "").strip()
    coin_id = str(entry.get("coinId") or "").strip()
    if not address and not coin_id:
        return None
    if not address and ":" in key:
        chain, address = key.split(":", 1)
    if not address:
        return None

    # Concentration figures (private-wallet corrected + raw) from concentration.json.
    conc = (concentration.get("coins") or {}).get(coin_id) or {}
    if not isinstance(conc, dict):
        conc = {}

    metadata: dict[str, Any] = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "token",
        "entity": address,
        "token_address": address,
        "chain": chain or "unknown",
        "severity": "high",
        "category": "scam-signature",
        "source": "fraudcoins",
        "source_url": DATASET_URL,
        "knowledge_type": "holder_concentration",
        "labels": ["scam-signature", "holder-concentration"],
    }
    if coin_id:
        metadata["coin_id"] = coin_id
    measured = entry.get("measuredAt")
    if measured:
        metadata["measured_at"] = str(measured)

    # Owner privileges + structural properties. Preserve upstream ``None``
    # (= not reported) rather than coercing to False — the dataset explicitly
    # warns that null does not mean the capability is absent.
    for field in PRIVILEGE_FIELDS + STRUCTURAL_FIELDS:
        if field in entry:
            metadata[field] = entry[field]
    privileges_known = [f for f in PRIVILEGE_FIELDS if entry.get(f) is True]
    if privileges_known:
        metadata["live_privileges"] = privileges_known
        metadata["labels"] = sorted({*metadata["labels"], "owner-privilege"})

    for field, src_key in (("top10_pct", "top10Pct"), ("top10_pct_raw", "top10PctRaw")):
        value = conc.get(src_key)
        if isinstance(value, int | float):
            metadata[field] = float(value)
    if conc.get("flagged") is not None:
        metadata["flagged"] = bool(conc["flagged"])
    series = _pct_series(history, coin_id) if coin_id else []
    if series:
        metadata["top10_pct_series"] = series
        dates = [row[0] for row in series if isinstance(row, list) and row]
        if dates:
            metadata["measured_from"] = dates[0]
            metadata["measured_to"] = dates[-1]

    content_lines = [
        f"Token scam-signature record: {coin_id or address}",
        f"Chain: {chain or 'unknown'}",
        f"Token address: {address}",
    ]
    if "top10_pct" in metadata:
        content_lines.append(f"Top-10 private-wallet concentration: {metadata['top10_pct']}%")
    if "top10_pct_raw" in metadata:
        content_lines.append(f"Top-10 raw concentration: {metadata['top10_pct_raw']}%")
    for field in PRIVILEGE_FIELDS:
        value = entry.get(field)
        if value is True:
            content_lines.append(f"Owner privilege present: {field}")
        elif value is False:
            content_lines.append(f"Owner privilege absent: {field}")
    for field in STRUCTURAL_FIELDS:
        if field in entry and entry[field] is not None:
            content_lines.append(f"{field}: {entry[field]}")

    return IngestDoc(
        content="\n".join(content_lines) + "\n",
        doc_id=f"fraudcoins:{chain or 'unknown'}:{address}",
        metadata=metadata,
        doc_type="scam_signature",
        source="fraudcoins",
        title=f"Holder-concentration signature — {coin_id or address[:12]}",
        url=DATASET_URL,
    )


class FraudCoinsSignaturesSource(Source):
    """FraudCoins holder concentration + GoPlus contract privileges (CC BY 4.0)."""

    name = "fraudcoins_signatures"
    doc_type = "scam_signature"
    default_collection = DEFAULT_COLLECTION
    description = (
        "FraudCoins on-chain holder-concentration + GoPlus owner privileges (scam_signatures)"
    )
    timeout = 90.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        permissions = await _load(client, PERMISSIONS_URL)
        tokens = permissions.get("tokens") or {}
        if not isinstance(tokens, dict) or not tokens:
            log.warning("fraudcoins_permissions_empty")
            return []
        concentration = await _load(client, CONCENTRATION_URL)
        history = await _load(client, HISTORY_URL)

        docs: list[IngestDoc] = []
        for key, entry in tokens.items():
            doc = token_to_doc(key, entry, concentration, history)
            if doc is not None:
                docs.append(doc)
        if limit:
            docs = docs[:limit]
        log.info("fraudcoins_signatures_ok tokens=%d", len(docs))
        return docs


__all__ = [
    "CONCENTRATION_URL",
    "DATASET_URL",
    "DEFAULT_COLLECTION",
    "HISTORY_URL",
    "PERMISSIONS_URL",
    "PRIVILEGE_FIELDS",
    "RISK_FIELDS",
    "STRUCTURAL_FIELDS",
    "FraudCoinsSignaturesSource",
    "token_to_doc",
]
