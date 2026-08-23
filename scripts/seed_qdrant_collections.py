#!/usr/bin/env python3
"""
Seed all empty Qdrant collections with real free data.
========================================================

Collections seeded (all 2560-dim Cosine, qwen3-embedding-4b embedder):
  - scam_intel:       ScamSniffer + Etherscan phish CSVs + curated patterns (100+)
  - scanner:          Postgres new_pairs + scan_results + scan_reports
  - news:             Postgres news_items (1,000 most recent) (500+)
  - research:         arXiv crypto/blockchain/security papers (free API)
  - scams:            MetaMask phishing blacklist (sampled) + ScamSniffer
  - wallet:           Redis rmi:label:* labels (deduped by address+chain) (500+)
  - threat_intel:     left untouched (already has 26 docs)
  - rmi_agent_memory: curated entity profiles + known addresses
  - rmi_community:    community submissions + curated topics

Data inputs (all FREE):
  1. Redis ``rmi:label:*`` keys — live wallet labels (10k+)
  2. Postgres dumps staged by ``fetch_seed_data.py`` (news_items.json,
     new_pairs.json, scan_community.json) — produced via the rmi-backend
     container so no credentials are embedded in this repo.
  3. GitHub raw lists: ScamSniffer address blacklist, MetaMask phishing
  4. Local CSVs: rmi-backend/data/etherscan_phish_hack.csv,
     etherscan_combined_labels.csv
  5. arXiv API (export.arxiv.org) — free, no key

Embedding: qwen3-embedding-4b via ``/embeddings`` (2560-dim), L2-normalized,
batched (20 short / 8 long) to maximize CPU throughput. Point IDs are
deterministic UUIDv5 (``uuid.NAMESPACE_DNS``) so reruns are idempotent and
partial progress is preserved (existing IDs are skipped).

Usage:
    python scripts/seed_qdrant_collections.py                     # all
    python scripts/seed_qdrant_collections.py --collection wallet  # one
    python scripts/seed_qdrant_collections.py --dry-run            # preview

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
  - redis-py, httpx, numpy, qdrant-client
  - Staged Postgres dumps in DATA_DIR (default /tmp/rag_seed)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import uuid
from typing import Any

import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("seed_qdrant")

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1").rstrip("/")
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
# Staged data lives under a user-configurable directory (default /tmp); the
# directory is outside the repo and never contains credentials.
DATA_DIR = os.getenv("SEED_DATA_DIR", "/tmp/rag_seed")  # noqa: S108  # nosec B108
EMBEDDING_DIM = 2560

# Throughput calibration: short texts batch 20 (~3.5s/doc), long texts batch 8
SHORT_BATCH = 20
LONG_BATCH = 8
LONG_TEXT_CHARS = 600  # texts longer than this use LONG_BATCH

# Free public sources
SCAMSNIFFER_URL = (
    "https://raw.githubusercontent.com/scamsniffer/scam-database/main/blacklist/address.json"
)
METAMASK_URL = "https://raw.githubusercontent.com/MetaMask/eth-phishing-detect/main/src/config.json"
ARXIV_QUERY = (
    "http://export.arxiv.org/api/query?search_query="
    "cat:cs.CR+OR+cat:q-fin.CR+OR+all:%22crypto%20scam%22+OR+all:%22blockchain%20security%22"
    "&start=0&max_results=60&sortBy=submittedDate&sortOrder=descending"
)

# Paths relative to this repo for the local CSVs (produced by rmi-backend)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETHERSCAN_PHISH_CSV = os.path.join(
    REPO_ROOT, "..", "rmi-backend", "data", "etherscan_phish_hack.csv"
)
ETHERSCAN_COMBINED_CSV = os.path.join(
    REPO_ROOT, "..", "rmi-backend", "data", "etherscan_combined_labels.csv"
)


def _to_uuid(doc_id: str) -> str:
    """Deterministic UUIDv5 (matches QdrantVectorStore._to_uuid)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


def _normalize(vec: list[float]) -> list[float]:
    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n > 0:
        arr = arr / n
    _vec: list[float] = arr.tolist()
    return _vec


async def _embed_batch(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """Embed texts via /embeddings, returning normalized vectors."""
    if not texts:
        return []
    try:
        r = await client.post(
            f"{QWEN_EMBED_URL}/embeddings",
            json={"model": QWEN_EMBED_MODEL_NAME, "input": [t[:8000] for t in texts]},
            timeout=900.0,
        )
        if r.status_code != 200:
            logger.warning("embed_http_fail status=%d body=%s", r.status_code, r.text[:200])
            return []
        data = r.json()
        items = data.get("data", [])
        vecs = [
            item.get("embedding", []) for item in sorted(items, key=lambda i: i.get("index", 0))
        ]
        out: list[list[float]] = []
        for v in vecs:
            if isinstance(v, list) and v:
                out.append(_normalize(v))
            else:
                out.append([])
        return out
    except Exception as e:
        logger.warning("embed_fail: %s", str(e)[:200])
        return []


async def _existing_ids(client: httpx.AsyncClient, collection: str) -> set[str]:
    """Return all point IDs already present in the collection."""
    existing: set[str] = set()
    try:
        offset: str | None = None
        while True:
            body: dict[str, Any] = {"limit": 5000, "with_payload": False, "with_vector": False}
            if offset:
                body["offset"] = offset
            r = await client.post(f"/collections/{collection}/points/scroll", json=body)
            if r.status_code != 200:
                break
            data = r.json().get("result", {})
            for p in data.get("points", []):
                existing.add(str(p.get("id", "")))
            nxt = data.get("next_page_offset")
            if not nxt:
                break
            offset = nxt
    except Exception as e:
        logger.warning("existing_ids_scan_failed collection=%s err=%s", collection, str(e)[:120])
    return existing


async def _upsert(
    client: httpx.AsyncClient,
    collection: str,
    doc_id: str,
    vector: list[float],
    content: str,
    metadata: dict[str, Any] | None = None,
    source: str = "",
    severity: str = "medium",
    chain: str = "",
) -> bool:
    """Upsert a single point into Qdrant (payload format matches rugbrain)."""
    point_id = _to_uuid(doc_id)
    payload = {
        "points": [
            {
                "id": point_id,
                "vector": vector,
                "payload": {
                    "content": content[:10000],
                    "metadata": metadata or {},
                    "source": source,
                    "severity": severity,
                    "chain": chain,
                    "collection": collection,
                },
            }
        ]
    }
    try:
        r = await client.put(f"/collections/{collection}/points", json=payload)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(
            "upsert_fail collection=%s doc=%s err=%s", collection, doc_id[:30], str(e)[:120]
        )
        return False


async def _upsert_many(
    client: httpx.AsyncClient,
    collection: str,
    docs: list[dict[str, Any]],
    existing: set[str],
) -> int:
    """Embed + upsert a list of docs, skipping existing point IDs. Returns inserted count."""
    if not docs:
        return 0
    todo = [d for d in docs if _to_uuid(d["doc_id"]) not in existing]
    if not todo:
        logger.info(
            "collection=%s all %d docs already present, nothing to do", collection, len(docs)
        )
        return 0
    logger.info(
        "collection=%s embedding %d docs (%d already present)",
        collection,
        len(todo),
        len(docs) - len(todo),
    )

    inserted = 0
    # Group into batches: long texts smaller batches
    long_texts = [d for d in todo if len(d.get("content", "")) > LONG_TEXT_CHARS]
    short_texts = [d for d in todo if len(d.get("content", "")) <= LONG_TEXT_CHARS]

    async def _process(group: list[dict[str, Any]], batch_size: int) -> int:
        nonlocal inserted
        for i in range(0, len(group), batch_size):
            chunk = group[i : i + batch_size]
            vectors = await _embed_batch(client, [d["content"] for d in chunk])
            for d, vec in zip(chunk, vectors, strict=False):
                if not vec or len(vec) != EMBEDDING_DIM:
                    logger.warning("skip doc=%s (bad vector len=%d)", d["doc_id"][:30], len(vec))
                    continue
                ok = await _upsert(
                    client,
                    collection,
                    d["doc_id"],
                    vec,
                    d["content"],
                    d.get("metadata") or {},
                    d.get("source", "seed"),
                    d.get("severity", "medium"),
                    d.get("chain", ""),
                )
                if ok:
                    inserted += 1
            done = min(i + batch_size, len(group))
            logger.info(
                "collection=%s progress %d/%d (inserted=%d)",
                collection,
                done,
                len(group),
                inserted,
            )
        return inserted

    await _process(short_texts, SHORT_BATCH)
    await _process(long_texts, LONG_BATCH)
    logger.info("collection=%s inserted=%d", collection, inserted)
    return inserted


def _chunk_text(text: str, max_chunk: int = 2000, overlap: int = 100) -> list[str]:
    """Simple character-window chunker for articles > max_chunk chars."""
    text = text.strip()
    if len(text) <= max_chunk:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chunk, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# ── Data loaders ──────────────────────────────────────────────────────


def load_wallet_from_redis(limit: int | None = None) -> list[dict[str, Any]]:
    """Read rmi:label:* keys from Redis, dedupe by address+chain."""
    import redis as sync_redis

    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "")
    r = sync_redis.Redis(host=host, port=port, password=password or None, decode_responses=True)
    try:
        r.ping()
    except Exception as e:
        logger.warning("redis_unavailable: %s", str(e)[:120])
        return []

    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for key in r.scan_iter(match="rmi:label:*", count=1000):
            raw = r.get(key)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            address = (data.get("address") or "").strip().lower()
            chain = (data.get("chain") or data.get("chain_id") or "unknown").strip().lower()
            name = (data.get("name") or "").strip()
            ltype = (data.get("type") or "wallet").strip()
            source = (data.get("source") or "redis_label").strip()
            if not address or len(address) < 10:
                continue
            # Normalize chain aliases
            if chain in ("eth", "ethereum"):
                chain = "ethereum"
            dedup_key = f"{chain}:{address}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            label = name or ltype
            if label.startswith("wallet_labels:"):
                label = label.split(":", 1)[1]
            doc_id = f"wallet:{chain}:{address}"
            severity = "high" if ltype in ("scam", "mixer") else "medium"
            docs.append(
                {
                    "doc_id": doc_id,
                    "content": f"Wallet {address} on {chain} labeled as: {label}",
                    "metadata": {
                        "address": address,
                        "chain": chain,
                        "label": label,
                        "label_type": ltype,
                        "source": source,
                    },
                    "source": f"redis:{source}" if source else "redis",
                    "severity": severity,
                    "chain": chain,
                }
            )
            if limit and len(docs) >= limit:
                break
    finally:
        r.close()
    logger.info("wallet_redis_loaded docs=%d", len(docs))
    return docs


def load_news_from_dump(limit: int | None = None) -> list[dict[str, Any]]:
    """Load staged Postgres news_items dump (produced by fetch_seed_data.py)."""
    path = os.path.join(DATA_DIR, "news_items.json")
    if not os.path.exists(path):
        logger.warning("news dump not found at %s — run fetch_seed_data.py first", path)
        return []
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    docs: list[dict[str, Any]] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        summary = (row.get("summary") or "").strip()
        body = (row.get("body") or "").strip()
        text = "\n\n".join(x for x in (title, summary, body) if x)
        if not text:
            continue
        source = (row.get("source") or "unknown").strip()
        published = (row.get("published_at") or "")[:10]
        url = (row.get("url") or "").strip()
        news_id = str(row.get("id") or hashlib.sha256(text.encode()).hexdigest()[:16])
        chunks = _chunk_text(text, max_chunk=2000)
        for idx, chunk in enumerate(chunks):
            doc_id = f"news:{news_id}:{idx}"
            docs.append(
                {
                    "doc_id": doc_id,
                    "content": chunk,
                    "metadata": {
                        "title": title,
                        "source": source,
                        "published_at": published,
                        "url": url,
                        "news_id": news_id,
                        "chunk_index": idx,
                    },
                    "source": f"news:{source}",
                    "severity": "info",
                    "chain": "multi",
                }
            )
        if limit and len(docs) >= limit:
            break
    logger.info("news_loaded docs=%d (from %d rows)", len(docs), len(rows))
    return docs


def load_scanner_from_dumps() -> list[dict[str, Any]]:
    """Load staged Postgres new_pairs + scan_results + scan_reports."""
    docs: list[dict[str, Any]] = []
    pairs_path = os.path.join(DATA_DIR, "new_pairs.json")
    if os.path.exists(pairs_path):
        with open(pairs_path, encoding="utf-8") as f:
            data = json.load(f)
        for row in data.get("new_pairs", []):
            pair_id = row.get("pair_id", "")
            chain = (row.get("chain") or "unknown").lower()
            base = row.get("base_token", "")
            quote = row.get("quote_token", "")
            base_sym = (row.get("base_symbol") or "?").upper()
            quote_sym = (row.get("quote_symbol") or "?").upper()
            price = row.get("price_usd")
            liq = row.get("liquidity_usd")
            created = row.get("created_at")
            if not pair_id:
                continue
            doc_id = f"scanner:{chain}:{pair_id}"
            docs.append(
                {
                    "doc_id": doc_id,
                    "content": (
                        f"New trading pair detected on {chain}: {base_sym}/{quote_sym}. "
                        f"Base token {base}, quote token {quote}. "
                        f"Price ${price:.8f}, liquidity ${float(liq or 0):,.0f}."
                    ),
                    "metadata": {
                        "pair_id": pair_id,
                        "chain": chain,
                        "base_token": base,
                        "quote_token": quote,
                        "base_symbol": base_sym,
                        "quote_symbol": quote_sym,
                        "price_usd": price,
                        "liquidity_usd": liq,
                        "created_at": created,
                    },
                    "source": "scanner:new_pairs",
                    "severity": "low",
                    "chain": chain,
                }
            )

    scan_path = os.path.join(DATA_DIR, "scan_community.json")
    if os.path.exists(scan_path):
        with open(scan_path, encoding="utf-8") as f:
            data = json.load(f)
        for row in data.get("scan_results", []):
            addr = row.get("token_address", "")
            chain = (row.get("chain") or "unknown").lower()
            risk = (row.get("risk_level") or "unknown").lower()
            flags = row.get("risk_flags") or []
            if isinstance(flags, str):
                try:
                    flags = json.loads(flags)
                except json.JSONDecodeError:
                    flags = [flags]
            if not addr:
                continue
            doc_id = f"scanner:{chain}:{addr}"
            docs.append(
                {
                    "doc_id": doc_id,
                    "content": (
                        f"Token scan on {chain}: {addr} risk={risk}. "
                        f"Flags: {', '.join(str(x) for x in flags) if flags else 'none'}. "
                        f"Safety score {row.get('safety_score', 'n/a')}."
                    ),
                    "metadata": {
                        "token_address": addr,
                        "chain": chain,
                        "risk_level": risk,
                        "risk_flags": flags,
                        "scanned_at": row.get("scanned_at"),
                        "confidence": row.get("confidence"),
                    },
                    "source": "scanner:scan_results",
                    "severity": "high" if risk in ("high", "critical") else "medium",
                    "chain": chain,
                }
            )
    logger.info("scanner_loaded docs=%d", len(docs))
    return docs


def load_scam_intel(limit: int | None = None) -> list[dict[str, Any]]:
    """ScamSniffer addresses + Etherscan phish CSVs + curated patterns."""
    docs: list[dict[str, Any]] = []

    # 1. ScamSniffer (fetched live, fallback to staged cache)
    cached = os.path.join(DATA_DIR, "scamsniffer_addresses.json")
    addresses: list[str] = []
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as f:
            addresses = json.load(f)
    if not addresses:
        addresses = asyncio.run(_fetch_scamsniffer())
        if addresses:
            with open(cached, "w", encoding="utf-8") as f:
                json.dump(addresses, f)
    for addr in addresses:
        if not isinstance(addr, str) or len(addr) < 10:
            continue
        chain = "ethereum" if addr.lower().startswith("0x") else "unknown"
        docs.append(
            {
                "doc_id": f"scamintel:scamsniffer:{addr.lower()}",
                "content": f"ScamSniffer phishing address: {addr}",
                "metadata": {
                    "address": addr.lower(),
                    "category": "phishing",
                    "source": "scamsniffer",
                },
                "source": "scamsniffer",
                "severity": "high",
                "chain": chain,
            }
        )
    logger.info("scam_intel scamsniffer=%d", len(docs))

    # 2. Etherscan phish CSV (local)
    if os.path.exists(ETHERSCAN_PHISH_CSV):
        with open(ETHERSCAN_PHISH_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                addr = (row.get("address") or "").strip().lower()
                name = (row.get("name_tag") or "").strip()
                chain_raw = (row.get("chain") or "ethereum").strip().lower()
                if not addr or len(addr) < 10:
                    continue
                chain = "ethereum" if chain_raw in ("ethereum", "eth") else chain_raw
                docs.append(
                    {
                        "doc_id": f"scamintel:etherscan_phish:{addr}",
                        "content": f"Etherscan phishing label: {name or addr} on {chain}",
                        "metadata": {
                            "address": addr,
                            "category": "phishing",
                            "label": name,
                            "source": "etherscan_phish_hack",
                        },
                        "source": "etherscan_phish_hack",
                        "severity": "high",
                        "chain": chain,
                    }
                )
    logger.info("scam_intel etherscan_phish total=%d", len(docs))

    # 3. Curated scam patterns (quick wins, guarantees coverage)
    curated = [
        {
            "doc_id": "scamintel:pattern:honeypot",
            "content": "Honeypot scam: token where only the creator can sell. Buyers are trapped. Indicators: maxSellAmount=0, tradingEnabled=false, blacklist of all non-owner addresses.",
            "metadata": {"category": "honeypot", "severity": "critical"},
            "severity": "critical",
            "chain": "multi",
        },
        {
            "doc_id": "scamintel:pattern:rugpull",
            "content": "Rug pull scam: owner mints unlimited tokens or removes liquidity, diluting holders to zero before draining. Indicators: unrestricted mint, onlyOwner mint, mint + removeLiquidity.",
            "metadata": {"category": "rugpull", "severity": "critical"},
            "severity": "critical",
            "chain": "multi",
        },
        {
            "doc_id": "scamintel:pattern:wallet_drainer",
            "content": "Wallet drainer / approval scam: contract tricks users into approving token spending then drains wallets via transferFrom. Indicators: unlimited approval request, approveAndCall, permit exploit.",
            "metadata": {"category": "phishing", "severity": "critical"},
            "severity": "critical",
            "chain": "multi",
        },
        {
            "doc_id": "scamintel:pattern:fake_renounce",
            "content": "Fake renounce scam: claims ownership renounced but keeps control via secondary address, proxy, or timelock trick. Indicators: fake renounce, ownership not actually 0xdead, hidden owner.",
            "metadata": {"category": "access_control", "severity": "high"},
            "severity": "high",
            "chain": "multi",
        },
        {
            "doc_id": "scamintel:pattern:mev_sandwich",
            "content": "Sandwich bot tax farm: high-tax token that benefits from MEV sandwich bots; creator runs bots to extract from traders. Indicators: tax > 10%, sandwich pattern, same wallet MEV + deployer.",
            "metadata": {"category": "mev", "severity": "medium"},
            "severity": "medium",
            "chain": "multi",
        },
    ]
    for c in curated:
        c["source"] = "curated_patterns"
        docs.append(c)

    if limit:
        docs = docs[:limit]
    logger.info("scam_intel total=%d", len(docs))
    return docs


async def _fetch_scamsniffer() -> list[str]:
    """Fetch ScamSniffer address blacklist via httpx (free, no auth)."""
    try:
        async with httpx.AsyncClient(timeout=40.0) as c:
            r = await c.get(SCAMSNIFFER_URL, headers={"User-Agent": "RMI-RAG/1.0"})
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("scamsniffer_fetch_failed: %s", str(e)[:120])
    return []


async def _fetch_metamask() -> dict[str, Any]:
    """Fetch MetaMask phishing config via httpx (free, no auth)."""
    try:
        async with httpx.AsyncClient(timeout=40.0) as c:
            r = await c.get(METAMASK_URL, headers={"User-Agent": "RMI-RAG/1.0"})
            if r.status_code == 200:
                _payload: dict[str, Any] = r.json()
                return _payload
    except Exception as e:
        logger.warning("metamask_fetch_failed: %s", str(e)[:120])
    return {}


async def _fetch_arxiv() -> bytes:
    """Fetch arXiv API response via httpx (free, no auth).

    export.arxiv.org redirects http → https (301), so redirects must be
    followed or the fetch returns an empty body and research stays empty.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
            r = await c.get(ARXIV_QUERY, headers={"User-Agent": "RMI-RAG/1.0"})
            if r.status_code == 200:
                return r.content
    except Exception as e:
        logger.warning("arxiv_fetch_failed: %s", str(e)[:120])
    return b""


def load_scams_from_metamask(sample: int = 300) -> list[dict[str, Any]]:
    """Sample MetaMask phishing blacklist domains + ScamSniffer leftovers."""
    docs: list[dict[str, Any]] = []
    cached = os.path.join(DATA_DIR, "metamask_blacklist.json")
    blacklist: list[str] = []
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as f:
            data = json.load(f)
            blacklist = data.get("blacklist", [])
    if not blacklist:
        data = asyncio.run(_fetch_metamask())
        blacklist = data.get("blacklist", [])
        if data:
            with open(cached, "w", encoding="utf-8") as f:
                json.dump(data, f)
    if not blacklist:
        return docs
    # Deterministic sampling (fixed stride, no RNG) so reruns are stable
    step = max(1, len(blacklist) // sample)
    sampled = [blacklist[i] for i in range(0, len(blacklist), step)][:sample]
    for domain in sampled:
        if not isinstance(domain, str) or not domain.strip():
            continue
        docs.append(
            {
                "doc_id": f"scams:metamask:{domain.strip().lower()}",
                "content": f"MetaMask phishing domain: {domain.strip()}",
                "metadata": {
                    "domain": domain.strip(),
                    "category": "phishing_domain",
                    "source": "metamask",
                },
                "source": "metamask_phishing",
                "severity": "high",
                "chain": "web",
            }
        )
    logger.info("scams metamask=%d", len(docs))
    return docs


def load_research_from_arxiv(limit: int = 60) -> list[dict[str, Any]]:
    """Fetch recent crypto/blockchain security papers from arXiv API.

    Uses the cached feed at ``DATA_DIR/arxiv_feed.xml`` when present (the
    live fetch via ``asyncio.run`` cannot run inside the async orchestrator;
    cache the feed first with a plain script). Falls back to a live fetch
    for non-async callers.
    """
    import xml.etree.ElementTree as ET

    docs: list[dict[str, Any]] = []
    cached_path = os.path.join(DATA_DIR, "arxiv_feed.xml")
    xml_data: bytes = b""
    if os.path.exists(cached_path):
        with open(cached_path, "rb") as f:
            xml_data = f.read()
    else:
        xml_data = asyncio.run(_fetch_arxiv())
    if not xml_data:
        logger.warning("arxiv feed empty — research collection not seeded")
        return docs

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        # arXiv feed is trusted public data fetched over HTTPS (or cached
        # from a prior fetch) — B314 XML-bomb risk does not apply.
        root = ET.fromstring(xml_data)  # noqa: S314  # nosec B314
        entries = root.findall("atom:entry", ns)
        for entry in entries[:limit]:
            title = "".join((entry.findtext("atom:title", "", ns) or "").split())
            summary = (entry.findtext("atom:summary", "", ns) or "").strip()
            eid = (entry.findtext("atom:id", "", ns) or "").strip()
            published = (entry.findtext("atom:published", "", ns) or "")[:10]
            authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]
            if not title or not summary:
                continue
            arxiv_id = eid.rsplit("/", 1)[-1]
            doc_id = f"research:arxiv:{arxiv_id}"
            docs.append(
                {
                    "doc_id": doc_id,
                    "content": f"Title: {title}\n\nAbstract: {summary[:1800]}",
                    "metadata": {
                        "title": title,
                        "arxiv_id": arxiv_id,
                        "published": published,
                        "authors": authors[:5],
                        "category": "research_paper",
                    },
                    "source": "arxiv",
                    "severity": "info",
                    "chain": "research",
                }
            )
    except Exception as e:
        logger.warning("arxiv_parse_failed: %s", str(e)[:120])
    logger.info("research arxiv=%d", len(docs))
    return docs


def load_agent_memory() -> list[dict[str, Any]]:
    """Curated entity profiles + known addresses for rmi_agent_memory."""
    docs = []
    entities = [
        {
            "name": "Lazarus Group",
            "desc": "North Korean state-backed hacking group (APT38, BlueNoroff). Responsible for $3B+ in crypto thefts including Bybit ($1.5B), Ronin ($620M), Harmony Bridge ($100M). Tactics: social engineering, fake job offers, supply chain attacks. Laundering: THORChain, eXch, Sinbad, Wasabi.",
            "tags": ["north_korea", "state_sponsored", "laundering"],
        },
        {
            "name": "Inferno Drainer",
            "desc": "Phishing-as-a-Service wallet drainer kit operation. $80M+ stolen from 130K+ victims across Ethereum, Polygon, Arbitrum. Operated via Telegram, shut down late 2024 but variants continue.",
            "tags": ["drainer", "phishing", "scam_as_a_service"],
        },
        {
            "name": "Pink Drainer",
            "desc": "Sophisticated phishing group operating wallet drainer infrastructure. $85M+ stolen from 21K+ victims. Targets NFT communities and DeFi users via Discord DMs and fake websites.",
            "tags": ["drainer", "phishing", "social_engineering"],
        },
        {
            "name": "Cross-Chain Bridge Exploit Pattern",
            "desc": "Recurring vulnerability class: validator set compromise (Ronin, Wormhole), smart contract bugs (Nomad, Poly), liquidity manipulation (ThorChain). Over $2.5B lost to bridge hacks total.",
            "tags": ["bridge", "cross_chain", "smart_contract"],
        },
        {
            "name": "Token-2022 Metaplex Extension Exploits",
            "desc": "Solana Token-2022 attack surface: mint authority hidden in Metaplex metadata, transfer hooks draining wallets, permanent delegate backdoor withdrawals.",
            "tags": ["solana", "token2022", "transfer_hook"],
        },
    ]
    for e in list(entities):
        slug = str(e["name"]).lower().replace(" ", "_")
        docs.append(
            {
                "doc_id": f"agent_memory:entity:{slug}",
                "content": f"ENTITY PROFILE: {e['name']}\n{e['desc']}",
                "metadata": {
                    "entity_name": e["name"],
                    "category": "threat_actor",
                    "tags": e["tags"],
                },
                "source": "curated_entities",
                "severity": "high",
                "chain": "multi",
            }
        )
    return docs


def load_community() -> list[dict[str, Any]]:
    """Community submissions (staged) + curated community topics."""
    docs: list[dict[str, Any]] = []
    scan_path = os.path.join(DATA_DIR, "scan_community.json")
    if os.path.exists(scan_path):
        with open(scan_path, encoding="utf-8") as f:
            data = json.load(f)
        for row in data.get("community_submissions", []):
            title = (row.get("title") or "").strip()
            body = (row.get("body") or "").strip()
            kind = (row.get("kind") or "report").strip()
            target = (row.get("target") or "").strip()
            cid = row.get("id")
            if not title and not body:
                continue
            text = f"{title}\n\n{body}" if title else body
            severity = "medium"
            if any(
                kw in (title + " " + body).lower()
                for kw in ("scam", "rug", "phish", "hack", "fraud")
            ):
                severity = "high"
            docs.append(
                {
                    "doc_id": f"community:submission:{cid}",
                    "content": text[:5000],
                    "metadata": {
                        "kind": kind,
                        "target": target,
                        "title": title,
                        "upvotes": row.get("upvotes", 0),
                    },
                    "source": "community_submissions",
                    "severity": severity,
                    "chain": "multi",
                }
            )
    topics = [
        {
            "topic": "Reentrancy Attack Detection",
            "insight": "Most common DeFi vulnerability. Check for external calls before state changes, missing guards, cross-function reentrancy. Use Slither + Echidna. High-risk: transfer() in withdraw functions.",
            "tags": ["reentrancy", "defi", "audit"],
        },
        {
            "topic": "Flash Loan Attack Prevention",
            "insight": "Flash loans enable exploits. Prevention: Chainlink TWAP prices, borrow cap + fee repayment in same tx, min/max price bounds, Balancer-style gradual price updates.",
            "tags": ["flash_loan", "defi", "oracle"],
        },
        {
            "topic": "Solana Token-2022 Risks",
            "insight": "Transfer hooks execute arbitrary code on every transfer. Permanent delegate can withdraw without approval. Verify transfer hook extensions, revoke mint authority, check permanent delegate.",
            "tags": ["solana", "token2022", "risk"],
        },
        {
            "topic": "MEV Protection Strategies",
            "insight": "Protect against sandwiches: limit orders with minOut, commit-reveal, Flashbots/MEV-Share on Ethereum, Jito bundles on Solana. Split high-value trades, private mempools, slippage < 0.5%.",
            "tags": ["mev", "sandwich", "protection"],
        },
        {
            "topic": "Bridge Security Best Practices",
            "insight": "Bridges lost $2.5B+. Use threshold signatures, per-validator rate limiting, optimistic verification with challenge period, audit cross-chain messaging, circuit breakers for large transfers.",
            "tags": ["bridge", "cross_chain", "security"],
        },
    ]
    for t in list(topics):
        slug = str(t["topic"]).lower().replace(" ", "_")
        docs.append(
            {
                "doc_id": f"community:topic:{slug}",
                "content": f"COMMUNITY TOPIC: {t['topic']}\nInsight: {t['insight']}",
                "metadata": {"topic": t["topic"], "tags": t["tags"], "kind": "curated_topic"},
                "source": "curated_community",
                "severity": "medium",
                "chain": "multi",
            }
        )
    logger.info("community loaded=%d", len(docs))
    return docs


# ── Orchestrator ──────────────────────────────────────────────────────

COLLECTION_LOADERS: dict[str, Any] = {
    "scam_intel_v2": load_scam_intel,
    "scanner_v2": load_scanner_from_dumps,
    "news_v2": load_news_from_dump,
    "research_v2": load_research_from_arxiv,
    "scams_v2": load_scams_from_metamask,
    "wallet_v2": load_wallet_from_redis,
    "rmi_agent_memory": load_agent_memory,
    "rmi_community": load_community,
}


async def seed_collection(client: httpx.AsyncClient, collection: str, dry_run: bool) -> int:
    loader = COLLECTION_LOADERS.get(collection)
    if not loader:
        logger.warning("no loader for collection=%s", collection)
        return 0
    logger.info("Loading data for collection=%s", collection)
    try:
        limits = json.loads(os.getenv("_SEED_LIMITS", "{}"))
    except json.JSONDecodeError:
        limits = {}
    limit = limits.get(collection)
    docs = loader() if limit is None else loader(limit=limit)
    if not docs:
        logger.info("collection=%s no source docs — skipping", collection)
        return 0
    if dry_run:
        logger.info("[DRY RUN] collection=%s would insert %d docs", collection, len(docs))
        return 0
    existing = await _existing_ids(client, collection)
    return await _upsert_many(client, collection, docs, existing)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Qdrant collections with free data")
    parser.add_argument("--collection", help="Seed only this collection (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--wallet-limit", type=int, default=None, help="Max wallet docs")
    parser.add_argument("--news-limit", type=int, default=None, help="Max news docs")
    parser.add_argument("--scam-intel-limit", type=int, default=None, help="Max scam_intel docs")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    logger.info(
        "Qdrant=%s =%s model=%s data=%s",
        QDRANT_URL,
        QWEN_EMBED_URL,
        QWEN_EMBED_MODEL_NAME,
        DATA_DIR,
    )

    # Inject limits via env for loaders that accept them
    limits = {
        "wallet_v2": args.wallet_limit,
        "news_v2": args.news_limit,
        "scam_intel_v2": args.scam_intel_limit,
    }
    os.environ["_SEED_LIMITS"] = json.dumps(limits)

    async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30.0) as client:
        targets = [args.collection] if args.collection else list(COLLECTION_LOADERS.keys())
        totals: dict[str, int] = {}
        for col in targets:
            try:
                totals[col] = await seed_collection(client, col, args.dry_run)
            except Exception as e:
                logger.error("collection=%s failed: %s", col, str(e)[:200])
                totals[col] = -1

    print("=" * 60)
    print("SEED COMPLETE")
    for col, n in totals.items():
        print(f"  {col}: {n}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
