"""CRPWarner rug-pull events → rug_events (manual ground-truth, with loss).

Source: https://github.com/CRPWarner/RugPull (no repo license — academic
artifact, USENIX Sec'22 "Contracted-related Rug Pull Warner"; verified 2026-09-28
that ``dataset/rugpullevents/rugpullevents.xlsx`` exists). The sheet is a
MANUALLY VERIFIED ground truth of real rug-pull events with the two fields the
corpus cannot otherwise compute: ``Classification`` (contract-related vs
transaction-related), ``Pattern`` (hidden mint / limiting sell / leaking token /
dumping / withdrawing liquidity / abandoning) and ``Loss`` in kUSD.

That makes it the highest-precision ``rug_events`` seed available: a documented
loss figure plus a typed mechanism, not prose.

Licence: the repository declares no SPDX licence. Ingest is gated on
``ALLOW_UNLICENSED_CORPORA=1`` (see the licence gate in the corpora report) —
without it the source returns ``[]`` rather than storing unlicensed content.

Emits one IngestDoc per rug event into ``rug_events`` (doc_type ``rug_event``),
STRUCTURED: ``loss_kusd`` / ``rug_pattern`` / ``rug_classification`` are
filterable payload fields.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "rug_events"
REPO = "CRPWarner/RugPull"
EVENTS_PATH = "dataset/rugpullevents/rugpullevents.xlsx"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/{EVENTS_PATH}"
REPO_URL = f"https://github.com/{REPO}"
DEFAULT_CAP = 2_000

# Column names as published in the sheet (verified via the repo README table).
COL_PROJECT = "Project Name"
COL_CLASSIFICATION = "Classification"
COL_PATTERN = "Pattern"
COL_LOSS = "Loss"
COL_SOURCE = "Source"
COL_OPEN_SOURCE = "Open Source"
COL_ENOUGH_INFO = "Enough Information"

# RugPullEnv gate: an unlicensed corpus is never stored unless explicitly opted in.
ALLOW_ENV = "ALLOW_UNLICENSED_CORPORA"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _loss_to_usd_k(value: Any) -> float | None:
    """Parse the ``Loss`` column (kUSD per the README) into a float."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def event_to_doc(row: dict[str, Any]) -> IngestDoc | None:
    """One ground-truth rug event → ``rug_events`` IngestDoc."""
    project = _text(row.get(COL_PROJECT))
    if not project:
        return None
    classification = _text(row.get(COL_CLASSIFICATION))
    pattern = _text(row.get(COL_PATTERN))
    loss_kusd = _loss_to_usd_k(row.get(COL_LOSS))
    source_ref = _text(row.get(COL_SOURCE))
    slug = "".join(c if c.isalnum() else "-" for c in project.lower()).strip("-") or "event"

    metadata: dict[str, Any] = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "project",
        "entity": project,
        "project": project,
        "chain": "multi",
        "severity": "critical",
        "category": "rug-pull",
        "source": "crpwarner",
        "source_url": REPO_URL,
        "knowledge_type": "ground_truth_rug_event",
        "labels": ["rugpull", "manual-groundtruth"],
    }
    if classification:
        metadata["rug_classification"] = classification
    if pattern:
        metadata["rug_pattern"] = pattern
    if loss_kusd is not None:
        metadata["loss_kusd"] = loss_kusd
    if source_ref:
        metadata["reference"] = source_ref
    if _text(row.get(COL_OPEN_SOURCE)):
        metadata["open_source"] = _text(row.get(COL_OPEN_SOURCE))
    if _text(row.get(COL_ENOUGH_INFO)):
        metadata["enough_information"] = _text(row.get(COL_ENOUGH_INFO))

    lines = [f"Verified rug-pull event: {project}"]
    if classification:
        lines.append(f"Classification: {classification}")
    if pattern:
        lines.append(f"Pattern: {pattern}")
    if loss_kusd is not None:
        lines.append(f"Economic loss: {loss_kusd} kUSD")
    if source_ref:
        lines.append(f"Reference: {source_ref}")

    return IngestDoc(
        content="\n".join(lines) + "\n",
        doc_id=f"crpwarner:rug_event:{slug}",
        metadata=metadata,
        doc_type="rug_event",
        source="crpwarner",
        title=f"Verified rug pull — {project}",
        url=REPO_URL,
    )


def parse_workbook(data: bytes) -> list[dict[str, Any]]:
    """Parse the xlsx workbook into a list of row dicts (empty on failure)."""
    try:
        import pandas as pd

        return pd.read_excel(io.BytesIO(data)).to_dict(orient="records")
    except Exception as e:
        log.warning("crpwarner_parse_failed err=%s", str(e)[:200])
        return []


class CRPWarnerRugEventsSource(Source):
    """CRPWarner manual ground-truth rug-pull events with loss figures."""

    name = "crpwarner_rug_events"
    doc_type = "rug_event"
    default_collection = DEFAULT_COLLECTION
    description = "CRPWarner manually-verified rug-pull ground truth (pattern + loss_kusd)"
    timeout = 120.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        if os.getenv(ALLOW_ENV, "").strip() not in {"1", "true", "yes"}:
            log.warning("crpwarner_skipped_unlicensed set %s=1 to ingest", ALLOW_ENV)
            return []
        cap = min(limit, DEFAULT_CAP) if limit else DEFAULT_CAP
        try:
            r = await client.get(RAW_URL, timeout=self.timeout)
            if r.status_code != 200:
                log.warning("crpwarner_non200 status=%d", r.status_code)
                return []
            data = r.content
        except Exception as e:
            log.warning("crpwarner_fetch_failed err=%s", str(e)[:200])
            return []

        docs: list[IngestDoc] = []
        for row in parse_workbook(data):
            doc = event_to_doc(row)
            if doc is not None:
                docs.append(doc)
                if len(docs) >= cap:
                    break
        log.info("crpwarner_rug_events_ok events=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "ALLOW_ENV",
    "COL_CLASSIFICATION",
    "COL_LOSS",
    "COL_PATTERN",
    "COL_PROJECT",
    "DEFAULT_COLLECTION",
    "EVENTS_PATH",
    "RAW_URL",
    "CRPWarnerRugEventsSource",
    "event_to_doc",
    "parse_workbook",
]
