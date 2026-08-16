"""openphish.com feed — real-time phishing URL feed (scam_intel_v2).

Source: https://openphish.com/feed.txt — a continuously updated,
newline-separated feed of active phishing URLs (AI/ML detected, incl.
crypto-phishing). The endpoint 302-redirects to the actual feed; the base
``httpx.AsyncClient`` follows redirects (verified live: 302→200).

The feed is generic (non-crypto-specific), so like the Phishing.Database
feed in ``blacklists.py`` it is capped lower and labeled at ``high`` (not
``critical``) severity so ``scam_intel_v2`` stays focused on Web3 intel.

Design rules (consistent with ``base.py`` / ``blacklists.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``openphish_feed:<hash>``) keeps re-ingestion
    idempotent.
  * Credentials: none.
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "scam_intel_v2"
DEFAULT_CAP = 2_000
FEED_URL = "https://openphish.com/feed.txt"
SOURCE_URL = "https://openphish.com"


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _clean_domain(url: str) -> str:
    """Best-effort domain from a URL (strip scheme/path/port)."""
    value = url.strip().lower()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.split("/")[0].split("?")[0].split("#")[0]
    if ":" in value:
        value = value.split(":")[0]
    return value.strip(".")


def parse_feed_lines(text: str) -> list[str]:
    """Parse newline-separated phishing URLs (skips blank/comment lines)."""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("<"):
            continue
        out.append(line)
    return out


def _make_doc(url: str) -> IngestDoc:
    domain = _clean_domain(url)
    metadata = {
        "collection": DEFAULT_COLLECTION,
        "entity_type": "domain",
        "entity": domain,
        "url": url,
        "labels": ["phishing"],
        "category": "phishing",
        "feed": "openphish",
        "source": "openphish_feed",
        "source_url": SOURCE_URL,
        "knowledge_type": "blacklist",
        "attack_vector": "phishing",
        "ecosystem": "multi",
        "severity": "high",  # generic (non-crypto-specific) feed
    }
    return IngestDoc(
        content=(f"openphish phishing URL: {url}\nLabels: phishing\nSource: {SOURCE_URL}\n"),
        doc_id=f"openphish_feed:{_content_hash(url)}",
        metadata=metadata,
        doc_type="scam_entity",
        source="openphish_feed",
        title=f"phishing URL ({domain})",
        url=SOURCE_URL,
    )


class OpenPhishSource(Source):
    """openphish.com feed — real-time phishing URL feed.

    Emits one ``IngestDoc`` per URL into ``scam_intel_v2`` (doc_type
    ``scam_entity``). Generic feed, capped + lower severity.
    """

    name = "openphish_feed"
    doc_type = "scam_entity"
    default_collection = DEFAULT_COLLECTION
    description = "openphish.com real-time phishing URL feed (scam_intel_v2, capped generic)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(FEED_URL, timeout=self.timeout)
            text = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("openphish_fetch_error err=%s", str(e)[:160])
            text = ""
        urls = parse_feed_lines(text)
        if not urls:
            log.warning("openphish_fetch_empty")
            return []
        log.info("openphish_feed_ok urls=%d (cap=%d)", len(urls), cap)
        return [_make_doc(u) for u in urls[:cap]]


__all__ = [
    "DEFAULT_COLLECTION",
    "OpenPhishSource",
    "parse_feed_lines",
]
