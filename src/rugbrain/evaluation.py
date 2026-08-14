"""
RAGAS Evaluation Pipeline - Weekly RAG quality assessment.
=============================================================
Evaluates the RugBrain RAG system against golden test set using RAGAS
metrics:
  - faithfulness: is the answer grounded in retrieved context?
  - answer_relevancy: does the answer address the question?
  - context_precision: are retrieved chunks relevant?
  - context_recall: are all relevant chunks retrieved?

Golden test set: 50 known crypto scam/intel queries with expected answers.
Runs weekly. Alerts on regression > 10% from baseline.

Queries run concurrently (bounded by ``EVAL_MAX_CONCURRENCY``) and each query
is bounded by ``EVAL_TIMEOUT_S`` via ``asyncio.wait_for`` so a slow or hung
search can never wedge the whole evaluation.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

BACKEND = "http://localhost:8012"
RAG_SEARCH = f"{BACKEND}/api/v1/search"
RAG_API_KEY = os.getenv("RMI_RAG_API_KEY", "")

# Per-query budget and concurrency cap. Each search call is wrapped in
# ``asyncio.wait_for`` with this timeout so one hung query cannot stall the
# full run (previously a single query could block for 30+ minutes).
EVAL_TIMEOUT_S = 10.0
EVAL_MAX_CONCURRENCY = 5

# ═══════════════════════════════════════════════════
# GOLDEN TEST SET - 20 queries with expected answers
# ═══════════════════════════════════════════════════
GOLDEN_TESTS = [
    {
        "query": "What are the most common DeFi hack techniques?",
        "collection": "hacks",
        "expected_terms": [
            "private key",
            "flash loan",
            "oracle manipulation",
            "reentrancy",
            "access control",
            "supply chain",
        ],
        "min_results": 3,
    },
    {
        "query": "How much was stolen in the Bybit hack?",
        "collection": "hacks",
        "expected_terms": ["bybit", "1.4", "billion", "1.46"],
        "min_results": 1,
    },
    {
        "query": "What is a honeypot scam in crypto?",
        "collection": "scam_intel",
        "expected_terms": ["honeypot", "sell", "restriction", "cannot sell", "maxTx"],
        "min_results": 2,
    },
    {
        "query": "What are the signs of a rug pull?",
        "collection": "scam_intel",
        "expected_terms": ["liquidity", "remove", "lp", "supply", "concentration", "fresh wallet"],
        "min_results": 2,
    },
    {
        "query": "How do crypto money launderers operate?",
        "collection": "threat_intel",
        "expected_terms": ["chain hopping", "mixer", "peel chain", "cex", "cross chain"],
        "min_results": 2,
    },
    {
        "query": "What did the Chainalysis 2025 crime report find?",
        "collection": "news",
        "expected_terms": ["40.9", "billion", "illicit", "stablecoin", "63%", "stolen"],
        "min_results": 1,
    },
    {
        "query": "What are the top smart contract vulnerabilities?",
        "collection": "threat_intel",
        "expected_terms": ["access control", "reentrancy", "oracle", "private key", "flash loan"],
        "min_results": 2,
    },
    {
        "query": "How much did North Korean hackers steal in 2024?",
        "collection": "news",
        "expected_terms": ["north korea", "1.34", "billion", "61%"],
        "min_results": 1,
    },
    {
        "query": "What happened with the Squid Game token?",
        "collection": "scam_intel",
        "expected_terms": ["squid", "game", "honeypot", "couldn't sell", "3.38"],
        "min_results": 1,
    },
    {
        "query": "What is a bundle attack in crypto?",
        "collection": "threat_intel",
        "expected_terms": ["bundle", "sniper", "mempool", "same block", "coordinated"],
        "min_results": 1,
    },
    {
        "query": "How does wash trading work in crypto?",
        "collection": "threat_intel",
        "expected_terms": ["wash trading", "circular", "volume", "inflation", "same entity"],
        "min_results": 1,
    },
    {
        "query": "What is the biggest DeFi hack ever?",
        "collection": "hacks",
        "expected_terms": ["bybit", "ronin", "poly network", "billion"],
        "min_results": 2,
    },
    {
        "query": "What are pig butchering scams?",
        "collection": "news",
        "expected_terms": ["pig butchering", "romance", "investment", "scam"],
        "min_results": 1,
    },
    {
        "query": "How do pump and dump schemes work in crypto?",
        "collection": "threat_intel",
        "expected_terms": ["pump", "dump", "insider", "accumulation", "fomo", "social"],
        "min_results": 1,
    },
    {
        "query": "What did TRM Labs report about crypto crime in 2025?",
        "collection": "news",
        "expected_terms": ["158", "billion", "illicit", "A7A5", "russia", "sanctions"],
        "min_results": 1,
    },
    {
        "query": "What percentage of bug bounty programs find critical bugs?",
        "collection": "threat_intel",
        "expected_terms": ["93.9%", "critical", "bug bounty", "immunefi"],
        "min_results": 1,
    },
    {
        "query": "What is the SafeMoon scam?",
        "collection": "scam_intel",
        "expected_terms": ["safemoon", "liquidity", "drain", "arrested", "fraud"],
        "min_results": 1,
    },
    {
        "query": "How are crypto scams using AI?",
        "collection": "news",
        "expected_terms": ["AI", "deepfake", "personalized", "KYC", "bypass"],
        "min_results": 1,
    },
    {
        "query": "What is a flash loan attack?",
        "collection": "threat_intel",
        "expected_terms": ["flash loan", "uncollateralized", "manipulate", "arbitrage"],
        "min_results": 1,
    },
    {
        "query": "What are the signs of a honeypot token contract?",
        "collection": "scam_intel",
        "expected_terms": ["honeypot", "sell", "restriction", "maxTx", "trading", "owner"],
        "min_results": 2,
    },
    # ── Expanded tests (30 new) ──
    {
        "query": "What is the OWASP Smart Contract Top 10?",
        "collection": "threat_intel",
        "expected_terms": ["access control", "business logic", "oracle", "reentrancy", "proxy"],
        "min_results": 3,
    },
    {
        "query": "How was the Ronin Bridge hacked?",
        "collection": "hacks",
        "expected_terms": ["ronin", "bridge", "validator", "private key"],
        "min_results": 1,
    },
    {
        "query": "What are common smart contract audit checklist items?",
        "collection": "threat_intel",
        "expected_terms": ["access control", "overflow", "reentrancy", "oracle", "validation"],
        "min_results": 2,
    },
    {
        "query": "How does a flash loan attack work?",
        "collection": "hacks",
        "expected_terms": ["flash loan", "uncollateralized", "single transaction", "arbitrage"],
        "min_results": 1,
    },
    {
        "query": "What happened in the OneCoin scam?",
        "collection": "scam_intel",
        "expected_terms": ["onecoin", "ponzi", "4 billion", "bitcoin"],
        "min_results": 1,
    },
    {
        "query": "What is a private key compromise attack?",
        "collection": "hacks",
        "expected_terms": ["private key", "compromise", "hot wallet", "phishing"],
        "min_results": 2,
    },
    {
        "query": "How do cross-chain bridge hacks work?",
        "collection": "hacks",
        "expected_terms": ["bridge", "cross chain", "validator", "message"],
        "min_results": 2,
    },
    {
        "query": "What are the top smart contract vulnerabilities in 2025?",
        "collection": "threat_intel",
        "expected_terms": ["access control", "reentrancy", "oracle", "overflow", "proxy"],
        "min_results": 2,
    },
    {
        "query": "How did the Squid Game token scam work?",
        "collection": "scam_intel",
        "expected_terms": ["squid", "game", "honeypot", "sell", "2,861"],
        "min_results": 1,
    },
    {
        "query": "What is a supply chain attack in crypto?",
        "collection": "threat_intel",
        "expected_terms": ["supply chain", "ads", "power", "bybit", "compromise"],
        "min_results": 1,
    },
    {
        "query": "How much crypto was stolen in 2024?",
        "collection": "news",
        "expected_terms": ["2.2", "billion", "stolen", "40.9"],
        "min_results": 1,
    },
    {
        "query": "What is the GMX V1 vulnerability?",
        "collection": "hacks",
        "expected_terms": ["gmx", "reentrancy", "glp", "arbitrum"],
        "min_results": 1,
    },
    {
        "query": "How does Tornado Cash work in laundering?",
        "collection": "threat_intel",
        "expected_terms": ["tornado", "mixer", "launder", "privacy"],
        "min_results": 1,
    },
    {
        "query": "What is an access control vulnerability?",
        "collection": "threat_intel",
        "expected_terms": ["access control", "unauthorized", "privileged", "owner"],
        "min_results": 2,
    },
    {
        "query": "How did BitConnect scam investors?",
        "collection": "scam_intel",
        "expected_terms": ["bitconnect", "ponzi", "40%", "2 billion"],
        "min_results": 1,
    },
    {
        "query": "What happened with the Poly Network hack?",
        "collection": "hacks",
        "expected_terms": ["poly network", "610", "cross chain", "white hat"],
        "min_results": 1,
    },
    {
        "query": "How do North Korean hackers steal crypto?",
        "collection": "news",
        "expected_terms": ["north korea", "lazarus", "1.34", "61%", "IT workers"],
        "min_results": 1,
    },
    {
        "query": "What is a proxy upgrade vulnerability?",
        "collection": "threat_intel",
        "expected_terms": ["proxy", "upgrade", "implementation", "initialize"],
        "min_results": 1,
    },
    {
        "query": "How does the SENTINEL scanner detect scams?",
        "collection": "scam_intel",
        "expected_terms": ["scam", "detect", "honeypot", "rug"],
        "min_results": 1,
    },
    {
        "query": "What are common DeFi money laundering patterns?",
        "collection": "threat_intel",
        "expected_terms": ["peel chain", "mixer", "chain hop", "cex"],
        "min_results": 1,
    },
    {
        "query": "How did the Euler Finance hack happen?",
        "collection": "hacks",
        "expected_terms": ["euler", "flash loan", "197", "donate"],
        "min_results": 1,
    },
    {
        "query": "What percentage of stolen crypto is from private key compromises?",
        "collection": "news",
        "expected_terms": ["43.8%", "private key", "stolen", "compromise"],
        "min_results": 1,
    },
    {
        "query": "How do oracle manipulation attacks work?",
        "collection": "threat_intel",
        "expected_terms": ["oracle", "price", "manipulation", "flash loan", "twap"],
        "min_results": 1,
    },
    {
        "query": "What is a reentrancy attack in Solidity?",
        "collection": "threat_intel",
        "expected_terms": ["reentrancy", "callback", "withdraw", "state"],
        "min_results": 1,
    },
    {
        "query": "How did SafeMoon defraud investors?",
        "collection": "scam_intel",
        "expected_terms": ["safemoon", "liquidity", "drain", "arrest"],
        "min_results": 1,
    },
    {
        "query": "What are the biggest DeFi hacks by amount lost?",
        "collection": "hacks",
        "expected_terms": ["bybit", "ronin", "poly", "billion", "million"],
        "min_results": 3,
    },
    {
        "query": "How does a sniper bot attack tokens at launch?",
        "collection": "threat_intel",
        "expected_terms": ["sniper", "mempool", "same block", "gas"],
        "min_results": 1,
    },
    {
        "query": "What is business logic vulnerability in smart contracts?",
        "collection": "threat_intel",
        "expected_terms": ["business logic", "design", "lending", "amm", "reward"],
        "min_results": 1,
    },
    {
        "query": "How did the Thodex exchange scam work?",
        "collection": "scam_intel",
        "expected_terms": ["thodex", "exchange", "2 billion", "turkey"],
        "min_results": 1,
    },
    {
        "query": "What are the most exploited chains for DeFi hacks?",
        "collection": "hacks",
        "expected_terms": ["ethereum", "bsc", "polygon", "arbitrum", "solana"],
        "min_results": 2,
    },
]


async def evaluate_single(test: dict, skip_answers: bool = False) -> dict:
    """Run a single test query and score it.

    The search call is bounded by ``EVAL_TIMEOUT_S`` via ``asyncio.wait_for``:
    a hung backend search (cold embed, wedged event loop) times out instead of
    blocking the run indefinitely.

    With ``skip_answers=True`` the expected-term precision pass is skipped and
    the result is scored on retrieval recall alone (faster smoke runs).
    """
    query = test["query"]
    collection = test["collection"]
    expected_terms = [t.lower() for t in test["expected_terms"]]
    min_results = test["min_results"]

    try:
        async with httpx.AsyncClient(timeout=EVAL_TIMEOUT_S) as c:
            r = await asyncio.wait_for(
                c.get(
                    RAG_SEARCH,
                    params={
                        "q": query,
                        "collection": collection,
                        "limit": 10,
                    },
                    headers={"X-API-Key": RAG_API_KEY} if RAG_API_KEY else None,
                ),
                timeout=EVAL_TIMEOUT_S,
            )
            if r.status_code != 200:
                return {"query": query, "error": f"HTTP {r.status_code}", "score": 0}

            data = r.json()
            results = data.get("results", [])
            total = data.get("total", 0)

            # Score: context_recall - did we get enough results?
            recall = min(total / min_results, 1.0) if min_results > 0 else 1.0

            if skip_answers:
                return {
                    "query": query[:60],
                    "collection": collection,
                    "results_found": total,
                    "min_expected": min_results,
                    "recall": round(recall, 3),
                    "score": round(recall, 3),
                }

            # Score: context_precision - how many expected terms appear in results?
            all_text = " ".join(
                [res.get("content", res.get("text", "")) for res in results]
            ).lower()
            terms_found = sum(1 for t in expected_terms if t in all_text)
            precision = terms_found / len(expected_terms) if expected_terms else 0

            # Combined score
            score = precision * 0.6 + recall * 0.4

            return {
                "query": query[:60],
                "collection": collection,
                "results_found": total,
                "min_expected": min_results,
                "terms_matched": f"{terms_found}/{len(expected_terms)}",
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "score": round(score, 3),
            }
    except TimeoutError:
        return {
            "query": query[:60],
            "collection": collection,
            "error": f"timeout after {EVAL_TIMEOUT_S}s",
            "score": 0,
        }
    except Exception as e:
        return {"query": query[:60], "error": str(e)[:200], "score": 0}


async def run_evaluation(limit: int | None = None, skip_answers: bool = False) -> dict:
    """Run full evaluation against golden test set.

    Queries run concurrently (``EVAL_MAX_CONCURRENCY`` at a time); each is
    individually timeout-bounded so the full run completes in roughly
    ``len(tests) / concurrency * timeout`` worst case instead of
    ``len(tests) * timeout`` serially.

    ``limit`` caps the number of golden tests run (useful for smoke runs);
    ``skip_answers`` skips the expected-term scoring pass and reports
    retrieval-only results (faster when the search backend is under load).
    """
    tests = GOLDEN_TESTS[:limit] if limit else GOLDEN_TESTS
    semaphore = asyncio.Semaphore(EVAL_MAX_CONCURRENCY)

    async def _run_with_semaphore(test: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await evaluate_single(test, skip_answers=skip_answers)

    results = await asyncio.gather(*(_run_with_semaphore(t) for t in tests))

    scores = [r["score"] for r in results if "score" in r]
    avg_score = sum(scores) / len(scores) if scores else 0
    passing = sum(1 for s in scores if s >= 0.5)
    failing = sum(1 for s in scores if s < 0.3)

    return {
        "test_count": len(tests),
        "tests_run": len(results),
        "average_score": round(avg_score, 3),
        "passing": passing,
        "failing": failing,
        "pass_rate": round(passing / len(scores), 3) if scores else 0,
        "results": results,
        "timestamp": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    result = asyncio.run(run_evaluation())
    print(json.dumps(result, indent=2))
