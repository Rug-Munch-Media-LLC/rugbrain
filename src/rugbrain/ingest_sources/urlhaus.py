"""abuse.ch URLhaus — malicious URL CSV feed (scam_intel_v2).

Source: https://urlhaus.abuse.ch/downloads/csv_recent/ — a CSV dump of
recently reported malicious URLs (regenerated every 5 minutes). Columns:
id, dateadded, url, url_status, last_online, threat, tags, urlhaus_link,
reporter. The dump carries a ``#``-prefixed comment header which is skipped.

Threat classes include ``phishing`` and ``crypto_mining`` (plus
``malware_download`` etc.). To keep ``scam_intel_v2`` focused on Web3
intel, only crypto-relevant threat classes are kept (phishing,
crypto_mining, cryptojacking) — generic malware is out of scope.

Design rules (consistent with ``base.py`` / ``blacklists.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``urlhaus_feed:<hash>``) keeps re-ingestion
    idempotent.
  * Credentials: none.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "scam_intel_v2"
DEFAULT_CAP = 2_000
FEED_URL = "https://urlhaus.abuse.ch/downloads/csv_recent/"
SOURCE_URL = "https://urlhaus.abuse.ch"

# Keep scam_intel_v2 focused on Web3-relevant threat classes.
_THREAT_KEEP = {"phishing", "crypto_mining", "cryptojacking", "crypto-mining"}

# CSV columns (verified live 2026-08-16): id, dateadded, url, url_status,
# last_online, threat, tags, urlhaus_link, reporter.
_COL_ID = 0
_COL_DATE = 1
_COL_URL = 2
_COL_STATUS = 3
_COL_LAST_ONLINE = 4
_COL_THREAT = 5
_COL_TAGS = 6
_COL_LINK = 7


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _clean_domain(url: str) -> str:
    value = url.strip().lower()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.split("/")[0].split("?")[0].split("#")[0]
    if ":" in value:
        value = value.split(":")[0]
    return value.strip(".")


def parse_csv_rows(text: str) -> list[list[str]]:
    """Parse the CSV dump, skipping the ``#`` comment header."""
    lines = [ln for ln in (text or "").splitlines() if ln and not ln.startswith("#")]
    try:
        return [row for row in csv.reader(io.StringIO("\n".join(lines))) if row]
    except Exception:
        return []


def urlhaus_row_to_doc(row: list[str]) -> IngestDoc | None:
    """Normalize one URLhaus CSV row into a scam_entity IngestDoc.

    Returns ``None`` when the threat class is out of scope or the URL is
    missing.
    """
    if len(row) <= _COL_URL:
        return None
    url = str(row[_COL_URL]).strip()
    if not url:
        return None
    threat = str(row[_COL_THREAT]).strip().lower() if len(row) > _COL_THREAT else ""
    if threat and threat not in _THREAT_KEEP:
        return None
    tags = str(row[_COL_TAGS]).strip() if len(row) > _COL_TAGS else ""
    date_added = str(row[_COL_DATE]).strip() if len(row) > _COL_DATE else ""
    status = str(row[_COL_STATUS]).strip() if len(row) > _COL_STATUS else ""
    link = str(row[_COL_LINK]).strip() if len(row) > _COL_LINK else ""

    domain = _clean_domain(url)
    content_parts = [
        f"URLhaus malicious URL: {url}",
        f"Threat: {threat or 'unknown'}",
    ]
    if tags:
        content_parts.append(f"Tags: {tags}")
    if date_added:
        content_parts.append(f"Date added: {date_added}")
    if status:
        content_parts.append(f"Status: {status}")
    if link:
        content_parts.append(f"URLhaus link: {link}")

    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"urlhaus_feed:{_content_hash(url)}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "entity_type": "domain",
            "entity": domain,
            "url": url,
            "labels": [threat or "phishing"],
            "category": threat or "phishing",
            "feed": "urlhaus",
            "source": "urlhaus_feed",
            "source_url": SOURCE_URL,
            "knowledge_type": "blacklist",
            "attack_vector": threat or "phishing",
            "ecosystem": "multi",
            "severity": "high",
            "date_added": date_added,
            "url_status": status,
            "tags": tags,
        },
        doc_type="scam_entity",
        source="urlhaus_feed",
        title=f"URLhaus {threat or 'malicious'} URL ({domain})",
        url=link or SOURCE_URL,
    )


class URLHausSource(Source):
    """abuse.ch URLhaus — malicious URL CSV feed.

    Emits one ``IngestDoc`` per crypto-relevant URL into ``scam_intel_v2``
    (doc_type ``scam_entity``). Phishing / crypto-mining / cryptojacking
    threats only; generic malware is skipped.
    """

    name = "urlhaus_feed"
    doc_type = "scam_entity"
    default_collection = DEFAULT_COLLECTION
    description = "abuse.ch URLhaus malicious URL feed (phishing/crypto-mining, scam_intel_v2)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(FEED_URL, timeout=self.timeout)
            text = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("urlhaus_fetch_error err=%s", str(e)[:160])
            text = ""
        rows = parse_csv_rows(text)
        docs: list[IngestDoc] = []
        for row in rows:
            doc = urlhaus_row_to_doc(row)
            if doc:
                docs.append(doc)
            if len(docs) >= cap:
                break
        if not docs:
            log.warning("urlhaus_fetch_empty")
        else:
            log.info("urlhaus_feed_ok urls=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "URLHausSource",
    "parse_csv_rows",
    "urlhaus_row_to_doc",
]
