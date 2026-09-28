"""Project the scythe-radar dead-token archive into ``rug_events`` rows.

Source: ``scytheradar/scythe-radar-datasets``, ``data/dead-token-archive.csv``
(CC0-1.0, so redistribution and derivation are both fine — see
``licenses.py``; the registry has no entry, and CC0 is wider than the default
research-only default, so it is declared here as ``INTERNAL``).

``ingest_sources/scythe_radar_datasets.py`` already fetches and documents this
CSV. That module writes to the generic ingest path; this one is the part that
was missing — turning a bilingual archive into the **structured** event shape,
which is what makes "how many holders were still in this pool when it drained"
answerable.

The archive is bilingual (zh + en) and three of its numeric facts live only in
the free-text ``victims_note``. They are real, human-written figures, so they
are parsed rather than dropped:

  * ``链上持有人 103798`` / ``约 30 万持有人`` → ``holders_at_rug``.
    The 万 (10k) and 亿 (100M) suffixes are handled, so "about 300k holders"
    becomes 300000 — approximate, and flagged as such by ``holders_exact``.
  * ``单次砸盘套现 $2626 万`` → ``loss_usd`` (a documented cash-out).
  * ``峰值 TVL $4590 万`` → ``peak_tvl_usd`` (peak TVL, NOT a loss; kept under
    its own name so a peak is never read as a loss).

``rug_ts`` is left EMPTY for this source: the archive publishes a narrative
timeline (``部署 → 跑路``), not a date. Inventing one from a timeline is
exactly the fabrication this collection must not contain.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from rugbrain.rug_events.scythe_text import parse_holders, parse_money, parse_peak_tvl

SOURCE = "scythe_radar"
SOURCE_URL = "https://github.com/scytheradar/scythe-radar-datasets"
LICENSE = "CC0-1.0"

#: A curated, human-documented archive — strong provenance, but a narrative
#: rather than an on-chain measurement, so it sits below the SolRPDS rows.
CONFIDENCE = 0.9

#: The archive's ``outcome`` column holds the ZH narrative and the ``status``
#: column carries the zh status slug. Both map to a stable enum.
_OUTCOME = {
    "lp_drained": "lp_drained",
    "went_to_zero": "went_to_zero",
    "rugged_relaunch": "rugged_relaunch",
    "已拔池": "lp_drained",
    "已归零": "went_to_zero",
    "重启盘": "rugged_relaunch",
}


def _resolve_outcome(row: dict[str, str]) -> str:
    """Stable outcome enum, or the raw text when no slug maps.

    The ``outcome`` column is free prose in the zh language and the ``status``
    column carries the zh status slug, so both are tried against the map before
    falling back to the human-readable ``status_en`` string. Preserving the raw
    value beats dropping it.
    """
    for key in ("status", "outcome"):
        mapped = _OUTCOME.get((row.get(key) or "").strip())
        if mapped:
            return mapped
    return (row.get("status_en") or row.get("outcome") or "").strip()


def parse_csv(text: str) -> list[dict[str, str]]:
    if not text:
        return []
    try:
        return [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text))
        ]
    except csv.Error:
        return []


def build_event(row: dict[str, str]) -> dict[str, Any] | None:
    """One archive row -> one ``rug_events`` payload, or None without an address."""
    address = (row.get("contract_address") or "").strip()
    if not address:
        return None
    chain = (row.get("chain") or "").strip() or "unknown"
    name = (row.get("token_name_en") or row.get("token_name") or "").strip()
    note = (row.get("victims_note") or "").strip()

    event: dict[str, Any] = {
        "token": name,
        "token_address": address,
        "chain": chain,
        "deployer": "",
        "rug_ts": "",  # the archive has a timeline, never a date — left empty
        "deployer_prior_rugs": 0,
        "deployer_attributed": False,
        "source": SOURCE,
        "source_url": (row.get("detail_url") or "").strip() or SOURCE_URL,
        "confidence": CONFIDENCE,
        "severity": "critical",
        "category": "rug-pull",
        "event_id": f"{SOURCE}:{chain}:{address}",
    }
    outcome = _resolve_outcome(row)
    if outcome:
        event["rug_outcome"] = outcome
    mechanism = (row.get("mechanism_en") or row.get("mechanism") or "").strip()
    if mechanism:
        event["rug_mechanism"] = mechanism
    warn = (row.get("early_warning") or "").strip()
    if warn:
        event["early_warning"] = warn
    if (row.get("timeline_span") or "").strip():
        event["timeline"] = row["timeline_span"].strip()
    holders, exact = parse_holders(note)
    if holders is not None:
        event["holders_at_rug"] = holders
        event["holders_exact"] = exact
    loss = parse_money(note) if not parse_peak_tvl(note) else None
    if loss is not None:
        event["loss_usd"] = loss
    peak = parse_peak_tvl(note)
    if peak is not None:
        event["peak_tvl_usd"] = peak
    if (row.get("peak_tvl") or "").strip():
        event["peak_tvl_note"] = row["peak_tvl"].strip()
    return event


def build_events(csv_text: str) -> list[dict[str, Any]]:
    """Whole archive -> the list of event payloads."""
    out: list[dict[str, Any]] = []
    for row in parse_csv(csv_text):
        event = build_event(row)
        if event is not None:
            out.append(event)
    return out


__all__ = [
    "CONFIDENCE",
    "LICENSE",
    "SOURCE",
    "SOURCE_URL",
    "build_event",
    "build_events",
    "parse_csv",
    "parse_holders",
    "parse_money",
    "parse_peak_tvl",
]
