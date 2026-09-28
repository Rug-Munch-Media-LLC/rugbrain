"""Small JSON/CSV fetch helpers for the structured corpora ingesters.

Split out of ``fraudcoins_signatures`` / ``scythe_radar_datasets`` purely to
keep every module under the 200-line cap and to give one place where the
fail-open fetch contract lives:

  * ``load_json``  — GET + parse one JSON object (``{}`` on any failure).
  * ``load_csv``   — GET + parse a CSV payload into row dicts (``[]`` on failure).
  * ``get_text``   — GET raw text, returning ``""`` unless the status is 200.

Every helper swallows upstream failures by design: a flaky or missing source
must never abort a batch (same contract as ``base.Source.fetch``).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


async def get_text(client: httpx.AsyncClient, url: str) -> str:
    """GET *url* and return the body, or ``""`` when unavailable.

    The per-request deadline is the client's own (``Source.timeout``).
    """
    try:
        r = await client.get(url)
    except Exception as e:
        log.warning("corpora_fetch_failed url=%s err=%s", url, str(e)[:160])
        return ""
    if r.status_code != 200:
        log.debug("corpora_fetch_non200 url=%s status=%d", url, r.status_code)
        return ""
    return r.text or ""


async def load_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """GET + parse one JSON object; ``{}`` on any failure (fail-open)."""
    text = await get_text(client, url)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("corpora_json_parse_failed url=%s err=%s", url, str(e)[:160])
        return {}
    return data if isinstance(data, dict) else {}


def load_csv(text: str) -> list[dict[str, str]]:
    """Parse a CSV payload into stripped row dicts; ``[]`` on any failure."""
    if not text:
        return []
    try:
        return [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text))
        ]
    except Exception as e:
        log.warning("corpora_csv_parse_failed err=%s", str(e)[:160])
        return []


__all__ = ["get_text", "load_csv", "load_json"]
