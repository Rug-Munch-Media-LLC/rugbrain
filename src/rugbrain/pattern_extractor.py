"""LLM-based pattern extraction from crypto scam/hack articles.

Fetches an article URL, sends it to an LLM for structured extraction,
and ingests the resulting scam pattern into the Qdrant scam_patterns
collection for future retrieval.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx

from rugbrain.search_service import ingest_document

logger = logging.getLogger("rag.pattern_extractor")

EXTRACT_PROMPT = """Extract structured scam pattern from this crypto incident report.
Return ONLY valid JSON (no markdown, no explanation) with these fields:
{
  "scam_type": "honeypot|rugpull|phishing|exploit|flash_loan|governance_attack|exit_scam|other",
  "chain": "ethereum|solana|bsc|arbitrum|polygon|base|other",
  "deployer_address": "0x... or null",
  "victim_count": number or null,
  "loss_usd": number or null,
  "methods": ["techniques used"],
  "indicators": ["red flags for detection"],
  "contract_addresses": ["0x addresses mentioned"],
  "summary": "one-sentence structured summary for RAG indexing",
  "severity": "critical|high|medium|low",
  "similar_patterns": ["known pattern IDs this matches"]
}

Incident report:
{article_text}"""


async def extract_pattern_from_url(url: str) -> dict[str, Any]:
    """Fetch an article, extract scam pattern via LLM, ingest into Qdrant."""
    result: dict[str, Any] = {"url": url, "status": "error", "pattern": None}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": "RMI-RAG-PatternBot/1.0"})
            resp.raise_for_status()
            article_text = resp.text[:8000]
    except Exception as e:
        result["error"] = f"fetch_failed: {e}"
        return result

    try:
        pattern = await _call_llm(article_text)
        if pattern:
            doc_id = f"extracted_pattern_{_hash_url(url)}"
            await ingest_document(
                doc_id=doc_id,
                collection="scam_patterns",
                content=json.dumps(pattern),
                metadata={
                    "source": url,
                    "type": "llm_extracted",
                    "scam_type": pattern.get("scam_type", "unknown"),
                    "severity": pattern.get("severity", "medium"),
                },
            )
            result["status"] = "ingested"
            result["pattern"] = pattern
    except Exception as e:
        result["error"] = f"llm_or_ingest_failed: {e}"

    return result


async def _call_llm(article_text: str) -> dict[str, Any] | None:
    prompt = EXTRACT_PROMPT.format(article_text=article_text)
    try:
        from rugbrain.compat import generate_content

        raw = await generate_content(prompt, max_tokens=2048, temperature=0.3)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        return cast(dict[str, Any], json.loads(raw))
    except Exception as e:
        logger.warning("pattern_extractor: LLM call failed: %s", e)
        return None


def _hash_url(url: str) -> str:
    return str(hash(url) % 10**10)


__all__ = ["extract_pattern_from_url"]
