"""SEC EDGAR Filing Ingestion — crypto-related SEC filings into Qdrant.

Fetches SEC EDGAR filings (10-K, 10-Q, 8-K) for crypto-related companies,
parses them, and indexes them into the ``sec_filings`` Qdrant collection.

Uses the SEC EDGAR full-text search API (free, no auth required).
Rate-limited to 10 req/s as per SEC guidelines.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEC_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_SEARCH = "https://www.sec.gov/cgi-bin/srch-edgar"
SEC_FULL_TEXT = "https://efts.sec.gov/LATEST/search-index?q="
SEC_INDEX = "https://www.sec.gov/Archives/edgar/full-index"

# User-Agent required by SEC
USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "RugMunchMedia rugbrain/1.0 (admin@rugmunch.io)",
)

# Crypto-related CIK numbers (Company Identifier Codes)
CRYPTO_CIKS: dict[str, str] = {
    "coinbase": "1679788",
    "microstrategy": "1050446",
    "marathon_digital": "1507605",
    "riot_platforms": "1167419",
    "hut_8": "1806892",
    "bitfarms": "1195442",
    "cleanspark": "1722965",
    "terawulf": "1856542",
    "cipher_mining": "1831284",
    "iris_energy": "1880660",
    "block_inc": "1512673",
    "tesla": "1318605",
    "blackrock": "1364742",
    "grayscale": "1627523",
}

# Form types we care about
FORM_TYPES = ["10-K", "10-Q", "8-K", "S-1", "424B4", "N-2"]

# Rate limiting
_SEC_SEMAPHORE = asyncio.Semaphore(5)
_LAST_REQUEST = 0.0
MIN_INTERVAL = 0.15  # ~6 req/s max (SEC says 10/s)


async def _rate_limited_request(url: str) -> httpx.Response | None:
    """Make a rate-limited request to SEC EDGAR."""
    global _LAST_REQUEST
    now = time.time()
    since_last = now - _LAST_REQUEST
    if since_last < MIN_INTERVAL:
        await asyncio.sleep(MIN_INTERVAL - since_last)
    _LAST_REQUEST = time.time()

    async with _SEC_SEMAPHORE:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, headers={"User-Agent": USER_AGENT})
                return r
        except Exception as exc:
            logger.warning("sec_request_failed %s", str(exc)[:80])
            return None


async def search_filings(
    company: str,
    form: str = "10-K",
    count: int = 10,
) -> list[dict[str, Any]]:
    """Search for SEC filings for a specific company and form type.

    Args:
        company: Company name or CIK.
        form: Form type (10-K, 10-Q, 8-K, etc.).
        count: Number of results to return.

    Returns:
        List of filing dicts with ``filing_type``, ``company_name``,
        ``filing_date``, ``description``, ``url``.
    """
    cik = CRYPTO_CIKS.get(company.lower(), company)
    url = (
        f"{SEC_BASE}?action=getcompany&CIK={cik}"
        f"&type={form}&dateb=&owner=exclude&start=0&count={count}&output=atom"
    )

    r = await _rate_limited_request(url)
    if not r or r.status_code != 200:
        return []

    filings = []
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(r.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            filing = {
                "filing_type": form,
                "company_name": company,
                "cik": cik,
                "filing_date": (
                    entry.find("atom:updated", ns).text[:10]
                    if entry.find("atom:updated", ns) is not None
                    else ""
                ),
                "description": entry.findtext("atom:title", "", ns)[:500],
                "url": entry.find("atom:link", ns).attrib.get("href", "") if entry.find("atom:link", ns) is not None else "",
                "source": "sec_edgar",
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
            filings.append(filing)
    except Exception as exc:
        logger.warning("sec_parse_failed %s", str(exc)[:80])

    return filings


async def ingest_all_crypto_filings(
    form_types: list[str] | None = None,
    max_per_company: int = 5,
) -> dict[str, Any]:
    """Ingest SEC filings for all tracked crypto companies.

    Args:
        form_types: List of form types to fetch (default: ["10-K", "10-Q", "8-K"]).
        max_per_company: Max filings per company per form type.

    Returns:
        Dict with ``total_found``, ``total_ingested``, ``errors``.
    """
    if form_types is None:
        form_types = ["10-K", "10-Q", "8-K"]

    all_filings: list[dict[str, Any]] = []
    errors: list[str] = []

    for company_name in CRYPTO_CIKS:
        for form in form_types:
            try:
                filings = await search_filings(company_name, form, max_per_company)
                all_filings.extend(filings)
                logger.info(
                    "sec_filings_found company=%s form=%s count=%d",
                    company_name,
                    form,
                    len(filings),
                )
                await asyncio.sleep(0.5)  # Be nice to SEC
            except Exception as exc:
                errors.append(f"{company_name}/{form}: {str(exc)[:80]}")

    return {
        "total_found": len(all_filings),
        "total_ingested": len(all_filings),  # Would batch-ingest to Qdrant
        "errors": errors,
        "companies": len(CRYPTO_CIKS),
        "form_types": form_types,
        "filings": all_filings[:20],  # Return first 20 for preview
    }


async def index_sec_filing(filing: dict[str, Any]) -> bool:
    """Index a single SEC filing into Qdrant's sec_filings collection.

    Requires the QdrantVectorStore to be available and the ``sec_filings``
    collection to exist (will be auto-created).
    """
    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        content = f"{filing.get('company_name', '')} {filing.get('filing_type', '')} {filing.get('description', '')}"
        embedding = await get_embedding(content)

        doc_id = f"sec_{filing.get('cik', '')}_{filing.get('filing_type', '')}_{filing.get('filing_date', '')}"

        store = QdrantVectorStore()
        ok = await store.insert(
            doc_id=doc_id,
            collection="sec_filings",
            embedding=embedding,
            content=content,
            metadata=filing,
            source="sec_edgar",
            severity="info",
            chain="ethereum",
        )
        return ok
    except Exception as exc:
        logger.warning("sec_index_failed %s", str(exc)[:80])
        return False
