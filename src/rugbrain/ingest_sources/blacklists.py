"""Consolidated scam/phishing blacklist threat-intel source.

Aggregates free, no-auth Web3 phishing blacklists into labeled
address/domain entities (labels: ``phishing``, ``scam``, plus
``drainer`` / ``rugpull`` when the upstream comment text supports it) so
the ``scam_intel_v2`` / entity data reflects real-time blocklists.

Targets ``scam_intel_v2`` (the live curated scam-intelligence collection).
It deliberately does NOT write to ``known_scams`` — that collection is the
frozen 2022-06-28 CryptoScamDB snapshot and is historical-only per
``docs/COLLECTION_REGISTRY.md``; fresh blacklist entities belong in the
live collection so consumers do not misread them as current-activity
evidence.

Feeds (all fetched from ``raw.githubusercontent.com`` — not subject to the
unauthenticated GitHub API rate limit):

  * ``scamsniffer``       — scam-database ``blacklist/domains.json`` +
    ``blacklist/address.json`` (daily-updated Web3 phishing domains and
    addresses; free tier ships with a 7-day delay).
  * ``metamask``          — eth-phishing-detect ``src/config.json``
    ``blocklist`` array (community-maintained Web3 domain blocklist).
  * ``myetherwallet``     — ethereum-lists ``addresses-darklist.json``
    (addresses + comments) and ``urls-darklist.json`` (malicious URLs).
  * ``phishfort``         — phishfort-lists ``blacklists/domains.json`` +
    ``hotlist.json`` (counter-phishing domain lists; GitHub mirror is
    deprecated upstream — see [REVIEW] notes).
  * ``phishing_database`` — Phishing.Database ``phishing-domains-ACTIVE.txt``
    (generic — non-crypto — phishing domains; capped and lower severity).

Dead feeds tracked for future work (repos 404 as of 2026-08): Forta
``phishing-dataset`` (drainer wallet labels) and Apesec ``rugpull-registry``
(rug-pull creator wallets). Their labels remain part of the vocabulary.

Design rules (consistent with ``base.py`` / ``github.py``):

  * Fetch → normalize → emit one ``IngestDoc`` per address/domain entity.
  * Per-feed failure is fail-open: log a warning, continue the other feeds.
  * Deterministic ``doc_id`` (``<source>:<feed>:<entity_type>:<hash>``)
    keeps re-ingestion idempotent (the store layer converts the seed to a
    UUIDv5 point ID).
  * No hardcoded credentials — optional ``GITHUB_TOKEN`` / ``PROXY_URL``
    are read from the environment only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

RAW = "https://raw.githubusercontent.com"
GITHUB_RAW = RAW  # alias kept for readability in feed descriptors

DEFAULT_MAX_PER_SOURCE = 10_000
# Generic (non-crypto) feeds get a smaller cap so the collection stays
# focused on Web3-relevant intel.
PHISHING_DB_CAP = 2_000

# Canonical label vocabulary (drainer/rugpull are emitted when an upstream
# comment matches; no live feed for them today — see module docstring).
_LABEL_PHISHING = "phishing"
_LABEL_SCAM = "scam"
_LABEL_DRAINER = "drainer"
_LABEL_RUGPULL = "rugpull"

# Keyword → extra label for MEW darklist address comments.
_COMMENT_LABELS: tuple[tuple[str, str], ...] = (
    ("drain", _LABEL_DRAINER),
    ("rug", _LABEL_RUGPULL),
    ("exit", _LABEL_RUGPULL),
    ("scam", _LABEL_SCAM),
    ("fraud", _LABEL_SCAM),
)


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


def _is_address(value: str) -> bool:
    return len(value) >= 40 and value[:2] in ("0x", "0X")


# ── Per-feed parse functions (text → list[entity dicts]) ───────────────
def _parse_json_array(text: str) -> list[str]:
    data = json.loads(text or "[]")
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    if isinstance(data, dict):
        # MetaMask config.json keeps blocked domains under a "blacklist" key
        # (older docs say "blocklist"; accept both, never the fuzzy/whitelist).
        block = data.get("blacklist") or data.get("blocklist") or []
        return [str(x).strip() for x in block if str(x).strip()]
    return []


def _parse_mew_urls(text: str) -> list[dict[str, Any]]:
    """MEW urls-darklist.json is a list of ``{"id", "comment"}`` objects."""
    data = json.loads(text or "[]")
    out: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            entity = str(item.get("id") or "").strip()
            if entity:
                out.append(
                    {
                        "entity": entity,
                        "comment": str(item.get("comment") or "").strip(),
                        "date": str(item.get("date") or "").strip(),
                    }
                )
        elif isinstance(item, str) and item.strip():
            out.append({"entity": item.strip()})
    return out


def _parse_mew_addresses(text: str) -> list[dict[str, Any]]:
    data = json.loads(text or "[]")
    out: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        addr = str(item.get("address") or "").strip()
        if addr:
            out.append(
                {
                    "entity": addr,
                    "comment": str(item.get("comment") or "").strip(),
                    "date": str(item.get("date") or "").strip(),
                }
            )
    return out


def _parse_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


@dataclass(frozen=True)
class Feed:
    """Descriptor for one upstream blacklist feed."""

    key: str
    repo: str
    ref: str
    paths: tuple[str, ...]
    entity_type: str  # "address" | "domain"
    label: str
    severity: str
    parse: Callable[[str], Any]
    cap: int
    url: str  # canonical source URL for metadata


FEEDS: tuple[Feed, ...] = (
    Feed(
        key="scamsniffer_domains",
        repo="scamsniffer/scam-database",
        ref="main",
        paths=("blacklist/domains.json",),
        entity_type="domain",
        label=_LABEL_PHISHING,
        severity="critical",
        parse=_parse_json_array,
        cap=DEFAULT_MAX_PER_SOURCE,
        url="https://github.com/scamsniffer/scam-database",
    ),
    Feed(
        key="scamsniffer_addresses",
        repo="scamsniffer/scam-database",
        ref="main",
        paths=("blacklist/address.json",),
        entity_type="address",
        label=_LABEL_PHISHING,
        severity="critical",
        parse=_parse_json_array,
        cap=DEFAULT_MAX_PER_SOURCE,
        url="https://github.com/scamsniffer/scam-database",
    ),
    Feed(
        key="metamask_blocklist",
        repo="MetaMask/eth-phishing-detect",
        ref="main",
        paths=("src/config.json",),
        entity_type="domain",
        label=_LABEL_PHISHING,
        severity="critical",
        parse=_parse_json_array,
        cap=DEFAULT_MAX_PER_SOURCE,
        url="https://github.com/MetaMask/eth-phishing-detect",
    ),
    Feed(
        key="mew_addresses",
        repo="MyEtherWallet/ethereum-lists",
        ref="master",
        paths=("src/addresses/addresses-darklist.json",),
        entity_type="address",
        label=_LABEL_PHISHING,
        severity="critical",
        parse=_parse_mew_addresses,
        cap=DEFAULT_MAX_PER_SOURCE,
        url="https://github.com/MyEtherWallet/ethereum-lists",
    ),
    Feed(
        key="mew_urls",
        repo="MyEtherWallet/ethereum-lists",
        ref="master",
        paths=("src/urls/urls-darklist.json",),
        entity_type="domain",
        label=_LABEL_PHISHING,
        severity="critical",
        parse=_parse_mew_urls,
        cap=DEFAULT_MAX_PER_SOURCE,
        url="https://github.com/MyEtherWallet/ethereum-lists",
    ),
    Feed(
        key="phishfort_domains",
        repo="phishfort/phishfort-lists",
        ref="master",
        paths=("blacklists/domains.json", "blacklists/hotlist.json"),
        entity_type="domain",
        label=_LABEL_PHISHING,
        severity="critical",
        parse=_parse_json_array,
        cap=DEFAULT_MAX_PER_SOURCE,
        url="https://github.com/phishfort/phishfort-lists",
    ),
    Feed(
        key="phishing_database",
        repo="Phishing-Database/Phishing.Database",
        ref="master",
        paths=("phishing-domains-ACTIVE.txt",),
        entity_type="domain",
        label=_LABEL_PHISHING,
        severity="high",
        parse=_parse_lines,
        cap=PHISHING_DB_CAP,
        url="https://github.com/Phishing-Database/Phishing.Database",
    ),
)

# Env knobs — no hardcoded credentials, all overridable.
_MAX_PER_SOURCE = int(os.getenv("BLACKLIST_MAX_PER_SOURCE", str(DEFAULT_MAX_PER_SOURCE)))
_ENABLED = {f.strip() for f in os.getenv("BLACKLIST_SOURCES", "").split(",") if f.strip()}


class BlacklistsSource(Source):
    """Consolidated scam/phishing blacklist source (one doc per entity).

    Emits ``IngestDoc`` records with ``doc_type="scam_entity"`` and
    metadata carrying ``entity_type`` (address/domain), ``entity`` (the
    value), ``labels`` (phishing/scam/drainer/rugpull), and ``source_url``.
    """

    name = "blacklists"
    doc_type = "scam_entity"
    default_collection = "scam_intel_v2"
    description = "Consolidated Web3 phishing blacklists (ScamSniffer, MetaMask, MEW, PhishFort, Phishing.Database)"

    def _enabled(self, feed: Feed) -> bool:
        return not _ENABLED or feed.key in _ENABLED or feed.key.split("_")[0] in _ENABLED

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        feeds = [f for f in FEEDS if self._enabled(f)]
        if not feeds:
            log.warning("blacklists_no_feeds_enabled allowed=%s", _ENABLED or "all")
            return []

        # Spread a small caller limit across feeds so --limit 15 does not
        # parse the whole Phishing.Database file before truncating.
        per_feed: int | None = None
        if limit is not None:
            per_feed = max(1, limit // len(feeds))

        docs: list[IngestDoc] = []
        for feed in feeds:
            cap = min(feed.cap, _MAX_PER_SOURCE)
            if per_feed is not None:
                cap = min(cap, per_feed)
            docs.extend(await self._fetch_feed(client, feed, cap))
        return docs

    async def _fetch_feed(self, client: httpx.AsyncClient, feed: Feed, cap: int) -> list[IngestDoc]:
        """Fetch + normalize one feed; fail-open with a warning.

        All ``feed.paths`` are fetched and merged (dedup by doc_id) so
        multi-list feeds such as PhishFort's full+hotlist both contribute.
        """
        try:
            docs: list[IngestDoc] = []
            seen_ids: set[str] = set()
            for path in feed.paths:
                text = await self._get_raw(client, feed, path)
                if not text:
                    continue
                for doc in self._emit(feed, path, feed.parse(text), cap):
                    if doc.doc_id in seen_ids:
                        continue
                    seen_ids.add(doc.doc_id)
                    docs.append(doc)
                    if len(docs) >= cap:
                        break
                if len(docs) >= cap:
                    break
            if docs:
                log.info(
                    "blacklists_feed_ok feed=%s entities=%d (cap=%d)",
                    feed.key,
                    len(docs),
                    cap,
                )
            else:
                log.warning("blacklists_feed_empty feed=%s paths=%s", feed.key, feed.paths)
            return docs
        except Exception as e:  # fail-open: one bad feed never aborts the batch
            log.warning("blacklists_feed_failed feed=%s err=%s", feed.key, str(e)[:200])
            return []

    async def _get_raw(self, client: httpx.AsyncClient, feed: Feed, path: str) -> str:
        """Fetch a raw file with one retry on 429/5xx (GitHub raw is not
        subject to the core API rate limit; the retry covers transient 5xx)."""
        url = f"{GITHUB_RAW}/{feed.repo}/{feed.ref}/{path}"
        for attempt in range(2):
            try:
                r = await client.get(url, timeout=self.timeout)
                if r.status_code == 200:
                    return r.text or ""
                if r.status_code in (429, 500, 502, 503, 504):
                    log.debug(
                        "blacklists_raw_retry feed=%s status=%d attempt=%d",
                        feed.key,
                        r.status_code,
                        attempt,
                    )
                    continue
                log.debug(
                    "blacklists_raw_failed feed=%s status=%d path=%s",
                    feed.key,
                    r.status_code,
                    path,
                )
                return ""
            except Exception as e:
                log.debug("blacklists_raw_error feed=%s err=%s", feed.key, str(e)[:160])
        return ""

    def _emit(self, feed: Feed, path: str, parsed: Any, cap: int) -> list[IngestDoc]:
        """Normalize parsed feed payload into IngestDoc entities."""
        docs: list[IngestDoc] = []
        entries: list[dict[str, Any]] = []

        if feed.key == "mew_addresses":
            entries = [dict(e) for e in parsed]
        elif isinstance(parsed, list):
            entries = [{"entity": str(v)} for v in parsed if str(v).strip()]
        else:
            return docs

        seen: set[str] = set()
        for entry in entries:
            entity = str(entry.get("entity") or "").strip()
            if not entity:
                continue
            if feed.entity_type == "domain":
                entity = _clean_domain(entity)
            if not entity:
                continue
            # The Phishing.Database generic list contains non-Web3 phishing;
            # keep it labeled but distinct via its own source_url + severity.
            key = f"{feed.key}:{feed.entity_type}:{_content_hash(entity)}"
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > cap:
                break

            labels = [feed.label]
            if feed.key.startswith("mew_"):
                comment = str(entry.get("comment") or "")
                for kw, label in _COMMENT_LABELS:
                    if kw in comment.lower() and label not in labels:
                        labels.append(label)

            comment = str(entry.get("comment") or "")
            date = str(entry.get("date") or "")
            docs.append(
                self._make_doc(
                    feed=feed,
                    path=path,
                    entity=entity,
                    labels=labels,
                    comment=comment,
                    date=date,
                )
            )
        return docs

    def _make_doc(
        self,
        *,
        feed: Feed,
        path: str,
        entity: str,
        labels: list[str],
        comment: str,
        date: str,
    ) -> IngestDoc:
        primary = labels[0]
        content_parts = [
            f"{self.name} {primary} {feed.entity_type}: {entity}",
            f"Labels: {', '.join(labels)}",
            f"Source: {feed.url}",
        ]
        if comment:
            content_parts.append(f"Comment: {comment}")
        if date:
            content_parts.append(f"Reported: {date}")

        metadata: dict[str, Any] = {
            "collection": self.default_collection,
            "entity_type": feed.entity_type,
            "entity": entity,
            "labels": labels,
            "category": primary,
            "feed": feed.key,
            "source": self.name,
            "source_url": feed.url,
            "knowledge_type": "blacklist",
            "attack_vector": primary,
            "ecosystem": "multi",
            "severity": feed.severity,
        }
        if comment:
            metadata["comment"] = comment
        if date:
            metadata["reported_date"] = date

        doc_id = f"{self.name}:{feed.key}:{feed.entity_type}:{_content_hash(entity)}"
        return IngestDoc(
            content="\n".join(content_parts),
            doc_id=doc_id,
            metadata=metadata,
            doc_type=self.doc_type,
            source=self.name,
            title=f"{primary} {feed.entity_type} ({feed.key})",
            url=feed.url,
        )


__all__ = [
    "FEEDS",
    "BlacklistsSource",
    "_clean_domain",
]
