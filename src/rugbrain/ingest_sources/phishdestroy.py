"""phishdestroy/destroylist — real-time crypto phishing domain blacklist.

Source: https://github.com/phishdestroy/destroylist — a continuously updated
(roughly hourly) blacklist of crypto phishing / scam / drainer domains,
208k+ curated entries plus a larger community list. The project publishes
several raw files; this source consumes the curated ``list.json`` (a JSON
array of domain strings) and falls back to the community ``blocklist.txt``
(newline-separated domains) when ``list.json`` is unavailable.

Routes to ``scam_intel_v2`` (the live curated scam-intelligence collection),
same as the existing ``blacklists`` source — fresh phishing domains are
current-activity evidence, not historical ``known_scams`` data.

Design rules (consistent with ``base.py`` / ``blacklists.py``):

  * Fetch → normalize → emit one ``IngestDoc`` per domain entity.
  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``phishdestroy_destroylist:<hash>``) keeps
    re-ingestion idempotent.
  * Credentials: none — the feed is public raw.githubusercontent content.
"""

from __future__ import annotations

import hashlib
import json
import logging

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "scam_intel_v2"
DEFAULT_CAP = 10_000

# raw.githubusercontent.com is not subject to the core API rate limit.
LIST_URL = "https://raw.githubusercontent.com/phishdestroy/destroylist/main/list.json"
COMMUNITY_URL = (
    "https://raw.githubusercontent.com/phishdestroy/destroylist/main/community/blocklist.txt"
)
SOURCE_URL = "https://github.com/phishdestroy/destroylist"

_LABEL_PHISHING = "phishing"


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _clean_domain(value: str) -> str:
    """Normalize a domain: strip scheme/path/whitespace, lowercase."""
    value = value.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.split("/")[0].split("?")[0].strip(".")


def parse_list_json(text: str) -> list[str]:
    """Parse the curated ``list.json`` (JSON array of domain strings).

    Tolerant: also accepts an object with a ``domains``/``list`` key, and a
    plain newline list as a fallback so a shape change never crashes the
    source.
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return parse_lines(text)
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    if isinstance(data, dict):
        for key in ("domains", "list", "blocklist"):
            v = data.get(key)
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
    return []


def parse_lines(text: str) -> list[str]:
    """Parse a newline-separated domain list (skips blank + comment lines)."""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _make_doc(domain: str) -> IngestDoc:
    """Normalize one phishing domain into an IngestDoc entity."""
    domain = _clean_domain(domain)
    metadata = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "domain",
        "entity": domain,
        "labels": [_LABEL_PHISHING],
        "category": _LABEL_PHISHING,
        "feed": "destroylist",
        "source": "phishdestroy_destroylist",
        "source_url": SOURCE_URL,
        "knowledge_type": "blacklist",
        "attack_vector": "phishing",
        "ecosystem": "multi",
        "severity": "critical",
    }
    return IngestDoc(
        content=(
            f"phishdestroy_destroylist phishing domain: {domain}\n"
            f"Labels: phishing\n"
            f"Source: {SOURCE_URL}\n"
        ),
        doc_id=f"phishdestroy_destroylist:{_content_hash(domain)}",
        metadata=metadata,
        doc_type="scam_entity",
        source="phishdestroy_destroylist",
        title=f"phishing domain ({domain})",
        url=SOURCE_URL,
    )


class PhishDestroySource(Source):
    """phishdestroy/destroylist — real-time crypto phishing domain blacklist.

    Emits one ``IngestDoc`` per phishing domain into ``scam_intel_v2``
    (doc_type ``scam_entity``). Curated ``list.json`` is primary; the
    community ``blocklist.txt`` is a fail-open fallback.
    """

    name = "phishdestroy_destroylist"
    doc_type = "scam_entity"
    default_collection = DEFAULT_COLLECTION
    description = (
        "phishdestroy destroylist — real-time crypto phishing/scam domain blacklist (scam_intel_v2)"
    )
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(LIST_URL, timeout=self.timeout)
            text = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("phishdestroy_fetch_error url=%s err=%s", LIST_URL, str(e)[:160])
            text = ""
        domains = parse_list_json(text) if text else []
        if not domains:
            # Fail-open fallback to the community blocklist.
            try:
                r = await client.get(COMMUNITY_URL, timeout=self.timeout)
                community = r.text if r.status_code == 200 else ""
            except Exception as e:
                log.warning("phishdestroy_community_error err=%s", str(e)[:160])
                community = ""
            domains = parse_lines(community)
        if not domains:
            log.warning("phishdestroy_fetch_empty")
            return []
        log.info("phishdestroy_feed_ok entities=%d (cap=%d)", len(domains), cap)
        return [_make_doc(d) for d in domains[:cap]]


__all__ = [
    "DEFAULT_COLLECTION",
    "PhishDestroySource",
    "parse_lines",
    "parse_list_json",
]
