"""
RAG Historical Scam Ingestion - Rekt DB, Chainabuse, TRM, Elliptic
==================================================================
Ingests historical crypto scam and hack data into RAG collections.
Sources: Rekt DB (3K+ DeFi hacks), Chainabuse (scam reports),
TRM Labs (annual crime report), Elliptic (scam report),
SlowMist (hacked archive), Immunefi (bug bounties), CertiK (audits).

Collections fed:
  - defi_hacks: Rekt DB, SlowMist, Rekt News
  - rug_timeline: Chainabuse, SENTINEL
  - vuln_patterns: Immunefi, CertiK
  - crime_reports: TRM, Elliptic, Chainalysis
  - forensic_reports: All exploit post-mortems

Runs: weekly (Sunday 2am) for historical sources.
"""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime

import httpx

logger = logging.getLogger("rag.historical")

# ── Config ──
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
RAG_INGEST_URL = f"{BACKEND_URL}/api/v1/rag/ingest"
REQUEST_TIMEOUT = 30

# ── Source Definitions ──
SOURCES = {
    "rekt_db": {
        "name": "Rekt Database",
        "url": "https://de.fi/rekt-database",
        "collection": "defi_hacks",
        "content_type": "scam_report",
        "description": "3,000+ DeFi hacks since 2020 - the most comprehensive exploit database",
        "cadence": "weekly",
    },
    "chainabuse": {
        "name": "Chainabuse",
        "url": "https://chainabuse.com",
        "collection": "rug_timeline",
        "content_type": "scam_report",
        "description": "Community-reported scam addresses with narratives",
        "cadence": "weekly",
    },
    "slowmist_hacked": {
        "name": "SlowMist Hacked Archive",
        "url": "https://slowmist.com/en/#hacked",
        "collection": "defi_hacks",
        "content_type": "scam_report",
        "description": "Detailed exploit analysis from SlowMist security team",
        "cadence": "weekly",
    },
    "rekt_news": {
        "name": "Rekt News",
        "url": "https://rekt.news",
        "collection": "defi_hacks",
        "content_type": "scam_report",
        "description": "In-depth DeFi exploit post-mortems",
        "cadence": "weekly",
    },
    "immunefi": {
        "name": "Immunefi Bug Bounties",
        "url": "https://immunefi.com",
        "collection": "vuln_patterns",
        "content_type": "contract_code",
        "description": "Bug bounty reports - real vulnerability patterns",
        "cadence": "weekly",
    },
    "certik": {
        "name": "CertiK Audit Findings",
        "url": "https://certik.com",
        "collection": "vuln_patterns",
        "content_type": "contract_code",
        "description": "Professional smart contract audit findings",
        "cadence": "weekly",
    },
    "trm_crime_report": {
        "name": "TRM Labs Crypto Crime Report",
        "url": "https://www.trmlabs.com/reports-and-whitepapers/2026-crypto-crime-report",
        "collection": "crime_reports",
        "content_type": "annual_report",
        "description": "Annual crypto crime typologies and trends - $158B illicit volume in 2025",
        "cadence": "yearly",
    },
    "elliptic_scams": {
        "name": "Elliptic State of Crypto Scams",
        "url": "https://info.elliptic.co/hubfs/The%20state%20of%20crypto%20scams%202025/",
        "collection": "crime_reports",
        "content_type": "annual_report",
        "description": "Annual crypto scam typologies and case studies",
        "cadence": "yearly",
    },
    "honeypotdb": {
        "name": "HoneypotDB",
        "url": "https://honeypot.is/api/top-honeypots",
        "collection": "known_scams",
        "content_type": "scam_report",
        "description": "Community-curated honeypot token list",
        "cadence": "weekly",
    },
    "scamsniffer": {
        "name": "ScamSniffer",
        "url": "https://scamsniffer.io/reports",
        "collection": "known_scams",
        "content_type": "scam_report",
        "description": "Real-time phishing and scam reports",
        "cadence": "weekly",
    },
    "tokensniffer": {
        "name": "TokenSniffer",
        "url": "https://tokensniffer.com/api/tokens",
        "collection": "known_scams",
        "content_type": "scam_report",
        "description": "Automated token scam detection",
        "cadence": "weekly",
    },
}


# ── Scraper Functions ──


async def scrape_rekt_db() -> list[dict]:
    """
    Scrape Rekt DB for historical DeFi hacks.
    de.fi/rekt-database lists 3,000+ exploits with:
    - Project name, date, amount lost, chain, vulnerability type
    - Links to post-mortems on rekt.news
    """
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # Try the API endpoint first
            resp = await client.get("https://de.fi/api/rekt")
            if resp.status_code == 200:
                data = resp.json()
                for item in data[:500]:  # Limit per run
                    docs.append(
                        {
                            "text": f"DeFi Hack: {item.get('name', 'Unknown')} - "
                            f"${item.get('amount', 0):,.0f} lost on {item.get('chain', 'Unknown')}. "
                            f"Vulnerability: {item.get('vulnerability', 'Unknown')}. "
                            f"Date: {item.get('date', 'Unknown')}. "
                            f"Details: {item.get('description', '')}",
                            "source": "rekt_db",
                            "url": item.get("url", "https://de.fi/rekt-database"),
                            "category": "defi_hack",
                            "date": item.get("date", ""),
                            "chain": item.get("chain", ""),
                            "tags": [
                                item.get("vulnerability", ""),
                                item.get("chain", ""),
                                "defi_hack",
                            ],
                        }
                    )
    except Exception as e:
        logger.warning("Rekt DB scrape failed: %s", e)

    logger.info("Rekt DB: %s hacks scraped", len(docs))
    return docs


async def scrape_chainabuse() -> list[dict]:
    """
    Scrape Chainabuse for scam reports with addresses.
    chainabuse.com has community-reported scams with:
    - Address, chain, scam type, description, reporter
    """
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # Chainabuse has a public reports page
            resp = await client.get(
                "https://chainabuse.com/api/reports",
                params={"limit": 200, "sort": "newest"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("reports", []):
                    address = item.get("address", "")
                    docs.append(
                        {
                            "text": f"Scam Report: {item.get('title', 'Unknown')} - "
                            f"Address {address} on {item.get('chain', 'Unknown')}. "
                            f"Type: {item.get('scam_type', 'Unknown')}. "
                            f"Description: {item.get('description', '')}. "
                            f"Reported by: {item.get('reporter', 'Anonymous')}",
                            "source": "chainabuse",
                            "url": f"https://chainabuse.com/report/{item.get('id', '')}",
                            "category": "scam_report",
                            "date": item.get("created_at", ""),
                            "chain": item.get("chain", ""),
                            "tags": [
                                item.get("scam_type", ""),
                                item.get("chain", ""),
                                "scam_report",
                            ],
                        }
                    )
    except Exception as e:
        logger.warning("Chainabuse scrape failed: %s", e)

    logger.info("Chainabuse: %s reports scraped", len(docs))
    return docs


async def scrape_slowmist() -> list[dict]:
    """Scrape SlowMist Hacked Archive for detailed exploit analysis."""
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get("https://slowmist.com/en/api/hacked")
            if resp.status_code == 200:
                data = resp.json()
                for item in data[:200]:
                    docs.append(
                        {
                            "text": f"SlowMist Analysis: {item.get('title', 'Unknown')} - "
                            f"${item.get('amount', 0):,.0f} lost. "
                            f"Root cause: {item.get('root_cause', 'Unknown')}. "
                            f"Attack vector: {item.get('attack_vector', 'Unknown')}. "
                            f"Recommendations: {item.get('recommendations', '')}",
                            "source": "slowmist",
                            "url": item.get("url", "https://slowmist.com"),
                            "category": "defi_hack",
                            "date": item.get("date", ""),
                            "chain": item.get("chain", ""),
                            "tags": [
                                item.get("root_cause", ""),
                                item.get("attack_vector", ""),
                                "defi_hack",
                                "slowmist",
                            ],
                        }
                    )
    except Exception as e:
        logger.warning("SlowMist scrape failed: %s", e)

    logger.info("SlowMist: %s analyses scraped", len(docs))
    return docs


async def scrape_rekt_news() -> list[dict]:
    """Scrape Rekt News for in-depth exploit post-mortems."""
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # Rekt News has an RSS feed
            resp = await client.get("https://rekt.news/feed.xml")
            if resp.status_code == 200:
                import defusedxml.ElementTree as element_tree  # noqa: N813

                root = element_tree.fromstring(resp.text)
                for item in root.findall(".//item")[:50]:
                    title = item.findtext("title", "")
                    description = item.findtext("description", "")
                    link = item.findtext("link", "")
                    pub_date = item.findtext("pubDate", "")

                    # Extract amount from title (e.g., "$10M Rekt")
                    amount_match = re.search(r"\$(\d+(?:\.\d+)?[MB]?)", title)
                    amount = amount_match.group(0) if amount_match else "Unknown"

                    docs.append(
                        {
                            "text": f"Rekt News: {title} - {amount} lost. {description}",
                            "source": "rekt_news",
                            "url": link,
                            "category": "defi_hack",
                            "date": pub_date,
                            "tags": ["defi_hack", "rekt_news", "post_mortem"],
                        }
                    )
    except Exception as e:
        logger.warning("Rekt News scrape failed: %s", e)

    logger.info("Rekt News: %s articles scraped", len(docs))
    return docs


async def ingest_trm_report() -> list[dict]:
    """
    Ingest TRM Labs 2026 Crypto Crime Report.
    Key findings: $158B illicit volume, $2.87B stolen in 150 hacks,
    Russia-linked sanctions evasion via A7A5 stablecoin.
    """
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                "https://www.trmlabs.com/reports-and-whitepapers/2026-crypto-crime-report"
            )
            if resp.status_code == 200:
                # Extract key findings from the page
                text = resp.text

                # Parse key sections
                sections = re.split(r"<h[23][^>]*>", text)
                for section in sections:
                    # Strip HTML tags
                    clean = re.sub(r"<[^>]+>", " ", section)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    if len(clean) > 100:
                        docs.append(
                            {
                                "text": f"TRM 2026 Crypto Crime Report: {clean[:2000]}",
                                "source": "trm_labs",
                                "url": "https://www.trmlabs.com/reports-and-whitepapers/2026-crypto-crime-report",
                                "category": "crime_report",
                                "date": "2026-01-28",
                                "tags": [
                                    "crime_report",
                                    "trm_labs",
                                    "annual_2026",
                                    "sanctions",
                                    "hacks",
                                ],
                            }
                        )
    except Exception as e:
        logger.warning("TRM report ingest failed: %s", e)

    logger.info("TRM Report: %s sections ingested", len(docs))
    return docs


async def ingest_honeypotdb() -> list[dict]:
    """Ingest HoneypotDB top honeypots list."""
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get("https://honeypot.is/api/top-honeypots")
            if resp.status_code == 200:
                data = resp.json()
                for item in data[:200]:
                    token_addr = item.get("token_address", item.get("address", ""))
                    token_name = item.get("token_name", item.get("name", "Unknown"))
                    chain = item.get("chain", item.get("network", "ethereum"))
                    score = item.get("honeypot_score", item.get("score", "N/A"))
                    docs.append(
                        {
                            "text": f"HoneypotDB Token: {token_name} ({token_addr}) on {chain}. "
                            f"Honeypot score: {score}. "
                            f"Risk indicators: {item.get('indicators', '')}",
                            "source": "honeypotdb",
                            "url": f"https://honeypot.is/{chain}/{token_addr}",
                            "category": "honeypot_token",
                            "date": datetime.now(UTC).isoformat(),
                            "chain": chain,
                            "tags": [
                                "honeypot",
                                chain,
                                "honeypotdb",
                                "known_scams",
                            ],
                        }
                    )
    except Exception as e:
        logger.warning("HoneypotDB ingest failed: %s", e)
        return []

    logger.info("HoneypotDB: %s tokens ingested", len(docs))
    return docs


async def ingest_scamsniffer() -> list[dict]:
    """Ingest ScamSniffer real-time phishing and scam reports."""
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get("https://scamsniffer.io/reports")
            if resp.status_code == 200:
                text = resp.text
                sections = re.split(r"<article[^>]*>", text)
                for section in sections[1:]:
                    clean = re.sub(r"<[^>]+>", " ", section)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    if len(clean) > 50:
                        docs.append(
                            {
                                "text": f"ScamSniffer Report: {clean[:2000]}",
                                "source": "scamsniffer",
                                "url": "https://scamsniffer.io/reports",
                                "category": "phishing_report",
                                "date": datetime.now(UTC).isoformat(),
                                "tags": [
                                    "phishing",
                                    "scam_report",
                                    "scamsniffer",
                                    "known_scams",
                                ],
                            }
                        )
    except Exception as e:
        logger.warning("ScamSniffer ingest failed: %s", e)
        return []

    logger.info("ScamSniffer: %s reports ingested", len(docs))
    return docs


async def ingest_tokensniffer() -> list[dict]:
    """Ingest TokenSniffer automated token scam detection."""
    docs = []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get("https://tokensniffer.com/api/tokens")
            if resp.status_code == 200:
                data = resp.json()
                tokens = data if isinstance(data, list) else data.get("tokens", [])
                for item in tokens[:200]:
                    token_addr = item.get("address", "")
                    token_name = item.get("name", "Unknown")
                    chain = item.get("chain", item.get("network", "ethereum"))
                    risks = item.get("risks", [])
                    risk_str = ", ".join(risks) if risks else "None"
                    docs.append(
                        {
                            "text": f"TokenSniffer Scan: {token_name} ({token_addr}) on {chain}. "
                            f"Detected risks: {risk_str}. "
                            f"Scam probability: {item.get('scam_probability', 'N/A')}. "
                            f"Flags: {item.get('flags', '')}",
                            "source": "tokensniffer",
                            "url": f"https://tokensniffer.com/token/{chain}/{token_addr}",
                            "category": "scam_token",
                            "date": datetime.now(UTC).isoformat(),
                            "chain": chain,
                            "tags": [
                                "scam_detection",
                                chain,
                                "tokensniffer",
                                "known_scams",
                            ],
                        }
                    )
    except Exception as e:
        logger.warning("TokenSniffer ingest failed: %s", e)
        return []

    logger.info("TokenSniffer: %s tokens ingested", len(docs))
    return docs


# ── Master Ingestion Orchestrator ──


async def ingest_historical_source(source_id: str) -> dict:
    """Ingest a single historical source into RAG."""
    source = SOURCES.get(source_id)
    if not source:
        return {"error": f"Unknown source: {source_id}"}

    logger.info("Ingesting %s → %s", source["name"], source["collection"])

    # Scrape
    scrapers = {
        "rekt_db": scrape_rekt_db,
        "chainabuse": scrape_chainabuse,
        "slowmist_hacked": scrape_slowmist,
        "rekt_news": scrape_rekt_news,
        "trm_crime_report": ingest_trm_report,
        "honeypotdb": ingest_honeypotdb,
        "scamsniffer": ingest_scamsniffer,
        "tokensniffer": ingest_tokensniffer,
    }

    scraper = scrapers.get(source_id)
    if not scraper:
        return {"error": f"No scraper for {source_id}", "source": source_id}

    try:
        docs = await scraper()
    except Exception as e:
        return {"error": str(e), "source": source_id, "docs_scraped": 0}

    if not docs:
        return {"source": source_id, "docs_scraped": 0, "status": "empty"}

    # Chunk and dedup
    from rugbrain.app_chunking import chunk_batch  # external dependency

    chunks = chunk_batch(docs, source["content_type"])

    # Ingest into backend
    ingested = 0
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Batch in groups of 50
            for i in range(0, len(chunks), 50):
                batch = chunks[i : i + 50]
                resp = await client.post(
                    RAG_INGEST_URL,
                    json={
                        "documents": batch,
                        "collection": source["collection"],
                        "source": source_id,
                        "chunking": source["content_type"],
                    },
                )
                if resp.status_code == 200:
                    result = resp.json()
                    ingested += result.get("ingested", len(batch))
                else:
                    logger.warning(
                        "Ingest batch failed for %s: HTTP %s", source_id, resp.status_code
                    )
    except Exception as e:
        logger.error("Ingest HTTP failed for %s: %s", source_id, e)

    return {
        "source": source_id,
        "name": source["name"],
        "collection": source["collection"],
        "docs_scraped": len(docs),
        "chunks_created": len(chunks),
        "ingested": ingested,
        "status": "ok" if ingested > 0 else "partial",
    }


async def ingest_all_historical() -> dict:
    """Run full historical ingestion across all sources."""
    results = {}
    total_ingested = 0

    for source_id in SOURCES:
        logger.info("Starting historical ingest: %s", source_id)
        try:
            result = await ingest_historical_source(source_id)
            results[source_id] = result
            total_ingested += result.get("ingested", 0)
        except Exception as e:
            results[source_id] = {"error": str(e), "source": source_id}
            logger.error("Historical ingest failed for %s: %s", source_id, e)

    return {
        "status": "complete",
        "total_ingested": total_ingested,
        "sources": results,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── CLI Entry Point ──
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        source = sys.argv[1]
        result = asyncio.run(ingest_historical_source(source))
        print(json.dumps(result, indent=2))
    else:
        result = asyncio.run(ingest_all_historical())
        print(json.dumps(result, indent=2))
