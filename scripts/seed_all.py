#!/usr/bin/env python3
"""
RAG Seeder — comprehensive seed of all Qdrant collections with known
scam patterns, historic scam data, and entity profiles.

Purpose:
  Populate initially-empty Qdrant collections so the RAG system returns
  real results immediately. After seeding, the live subscriber/poller
  pipelines keep collections fresh.

Collections seeded:
  - threat_intel_v2:  Known scam patterns + historical scam reports
  - rmi_agent_memory:  Entity profiles, wallet labels, cross-chain identities
  - rmi_community:     Community-reported scams, discussion topics

Usage:
  python scripts/seed_all.py          # seed everything
  python scripts/seed_all.py --dry-run  # preview what would be seeded

Requires:
  - Qdrant running (default: http://rmi-qdrant-v2:6333 or QDRANT_URL env)
  - running (default: http://172.22.0.1:8004/v1 or QWEN_EMBED_URL env)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any

import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("seed_all")

QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant-v2:6333").rstrip("/")
QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1").rstrip("/")
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
BACKEND_URL = os.getenv("BACKEND_URL", "http://rmi-backend:8000")
EMBEDDING_DIM = 2560


# ── Embedding ──────────────────────────────────────────────────────────


async def _embed(text: str) -> list[float] | None:
    """Get embedding from qwen3-embedding-4b via /embeddings.

    Uses the OpenAI-compatible ``/embeddings`` endpoint with ``input``
    as a list (faster, supports batching).
    Returns L2-normalized 2560-dim vector or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{QWEN_EMBED_URL}/embeddings",
                json={"model": QWEN_EMBED_MODEL_NAME, "input": [text[:8000]]},
            )
            if r.status_code != 200:
                logger.warning("embed_http_fail status=%d", r.status_code)
                return None
            data = r.json()
            items = data.get("data", [])
            if not items or not isinstance(items[0].get("embedding"), list):
                return None
            vec = items[0]["embedding"]
            # L2 normalize
            arr = np.asarray(vec, dtype=np.float32)
            n = float(np.linalg.norm(arr))
            if n > 0:
                arr = arr / n
            result: list[float] = arr.tolist()
            return result
    except Exception as e:
        logger.warning("embed_fail: %s", e)
        return None


async def _embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed multiple texts in a single batch call for efficiency.

    's ``/embeddings`` endpoint supports multiple inputs in one
    request, which avoids repeated model-load overhead.
    """
    if not texts:
        return []
    try:
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(
                f"{QWEN_EMBED_URL}/embeddings",
                json={"model": QWEN_EMBED_MODEL_NAME, "input": [t[:8000] for t in texts]},
            )
            if r.status_code != 200:
                logger.warning("batch_embed_http_fail status=%d", r.status_code)
                return await _embed_batch_fallback(texts)
            data = r.json()
            items = sorted(data.get("data", []), key=lambda i: i.get("index", 0))
            results = []
            for item in items:
                vec = item.get("embedding", [])
                if isinstance(vec, list) and vec:
                    arr = np.asarray(vec, dtype=np.float32)
                    n = float(np.linalg.norm(arr))
                    if n > 0:
                        arr = arr / n
                    results.append(arr.tolist())
                else:
                    results.append(None)
            return results
    except Exception as e:
        logger.warning("batch_embed_fail: %s — falling back to sequential", e)
        return await _embed_batch_fallback(texts)


async def _embed_batch_fallback(texts: list[str]) -> list[list[float] | None]:
    """Fallback: embed texts sequentially."""
    return [await _embed(t) for t in texts]


# ── Qdrant helpers ─────────────────────────────────────────────────────


async def _ensure_collection(name: str, client: httpx.AsyncClient) -> bool:
    """Ensure Qdrant collection exists with correct config."""
    try:
        r = await client.get(f"/collections/{name}")
        if r.status_code == 200:
            return True
    except Exception:
        logger.debug("ensure_collection_check_failed %s", name)
    payload = {"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}}
    try:
        r = await client.put(f"/collections/{name}", json=payload)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error("create_collection_fail %s: %s", name, e)
        return False


def _to_uuid(doc_id: str) -> str:
    """Deterministic UUID v5 from doc_id string."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


async def _upsert_point(
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
    """Upsert a single point into Qdrant."""
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
        logger.warning("upsert_fail %s/%s: %s", collection, doc_id[:20], e)
        return False


# ── Seed Data ──────────────────────────────────────────────────────────


KNOWN_SCAM_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "Honeypot - Sell Disabled",
        "description": "Token where only the creator can sell. Buyers are trapped. Implemented via maxSellAmount=0, tradingEnabled=false, or blacklist of all non-owner addresses.",
        "indicators": [
            "maxSellAmount=0",
            "tradingEnabled=false",
            "onlyOwner can transfer",
            "blacklist all addresses",
        ],
        "severity": "critical",
    },
    {
        "name": "Unlimited Mint / Rug Pull",
        "description": "Owner can mint unlimited tokens, diluting holders to zero before draining liquidity.",
        "indicators": [
            "unrestricted mint()",
            "onlyOwner mint",
            "no supply cap",
            "mint + removeLiquidity",
        ],
        "severity": "critical",
    },
    {
        "name": "Hidden Fee Manipulation",
        "description": "Owner can set fees to 100% after launch, making sells impossible or stealing all transfers.",
        "indicators": [
            "setTax(100)",
            "dynamic fee > 50%",
            "setFee external onlyOwner",
            "no max fee cap",
        ],
        "severity": "high",
    },
    {
        "name": "Liquidity Drain Backdoor",
        "description": "Hidden function allowing owner to remove all liquidity. Often disguised as 'manualSwap' or 'rescueToken'.",
        "indicators": [
            "manualSwap",
            "removeLiquidity",
            "drain",
            "sweep",
            "rescueToken with onlyOwner",
        ],
        "severity": "critical",
    },
    {
        "name": "Proxy Upgrade Trap",
        "description": "Upgradeable proxy where admin can change implementation to a malicious contract post-launch.",
        "indicators": [
            "upgradeTo",
            "upgradeToAndCall",
            "UUPS",
            "TransparentUpgradeableProxy",
            "changeAdmin",
        ],
        "severity": "high",
    },
    {
        "name": "Bundle/Sniper Launch",
        "description": "Creator uses bundling to acquire large supply at launch before others can buy, then dumps.",
        "indicators": [
            "multi-wallet buy at block 0",
            "same funder",
            "bundle detection",
            "60%+ supply in 3 wallets",
        ],
        "severity": "high",
    },
    {
        "name": "Honeypot - Max Tx Limit",
        "description": "Token with maxTxAmount set so low that only tiny amounts can be sold, trapping larger holders.",
        "indicators": [
            "maxTxAmount < 0.1% supply",
            "maxSellAmount micro",
            "anti-whale as antibot disguise",
        ],
        "severity": "high",
    },
    {
        "name": "Fake Renounce / Ownership Hide",
        "description": "Claims ownership renounced but keeps control via secondary address, proxy, or timelock trick.",
        "indicators": [
            "fake renounce",
            "ownership not actually 0xdead",
            "hidden owner",
            "renounceOwnership not called",
        ],
        "severity": "high",
    },
    {
        "name": "Sandwich Bot Tax Farm",
        "description": "Token with high taxes that benefits from MEV sandwich bots. Creator runs bots to extract from traders.",
        "indicators": ["tax > 10%", "sandwich pattern", "same wallet MEV + deployer"],
        "severity": "medium",
    },
    {
        "name": "Wallet Drainer / Approval Scam",
        "description": "Contract tricks users into approving token spending, then drains their wallets via transferFrom.",
        "indicators": [
            "unlimited approval request",
            "transferFrom in unverified contract",
            "approveAndCall",
            "permit exploit",
        ],
        "severity": "critical",
    },
]

HISTORICAL_HACKS: list[dict[str, Any]] = [
    {
        "name": "Bybit Hack 2025",
        "description": "Largest crypto hack in history. $1.5B stolen from Bybit cold wallet. Attacker used sophisticated social engineering + multi-sig exploit on Ethereum. Funds laundered through THORChain, eXch, and CryptoMixer.",
        "year": "2025",
        "amount_usd": 1500000000,
        "chain": "ethereum",
        "tags": ["exchange_hack", "cold_wallet", "social_engineering", "defi"],
    },
    {
        "name": "Radiant Capital Flash Loan Attack",
        "description": "Flash loan attack on Radiant Capital exploited a rounding error in the lending protocol, draining $50M across Arbitrum and BNB Chain. Attack involved manipulating exchange rates via precision loss.",
        "year": "2024",
        "amount_usd": 50000000,
        "chain": "arbitrum",
        "tags": ["flash_loan", "lending", "rounding_error"],
    },
    {
        "name": "Munchables Blast Exploit",
        "description": "Insider attack on Munchables, a Blast NFT game. A rogue developer added a backdoor to the smart contract and drained $62.5M. Funds recovered through negotiation.",
        "year": "2024",
        "amount_usd": 62500000,
        "chain": "blast",
        "tags": ["insider_threat", "backdoor", "nft", "gaming"],
    },
    {
        "name": "Wormhole Bridge Hack",
        "description": "Signature verification vulnerability in Wormhole bridge contract. Attacker forged validator signatures to mint 120,000 wETH ($325M) on Solana without collateral. Exploit targeted the cross-chain messaging verification.",
        "year": "2022",
        "amount_usd": 325000000,
        "chain": "solana",
        "tags": ["bridge", "signature_verification", "cross_chain"],
    },
    {
        "name": "FTX Collapse",
        "description": "Alameda Research used customer funds from FTX for risky trades. $8B+ in customer funds missing. Led to one of the largest fraud cases in crypto history. Founder Sam Bankman-Fried convicted.",
        "year": "2022",
        "amount_usd": 8000000000,
        "chain": "ethereum",
        "tags": ["exchange", "fraud", "cefi", "regulatory"],
    },
    {
        "name": "Ronin Bridge Hack (Axie )",
        "description": "Lazarus Group hacked Ronin bridge (Axie sidechain) by compromising 5 of 9 validator nodes using social engineering and fake job offers. $620M in USDC and ETH stolen.",
        "year": "2022",
        "amount_usd": 620000000,
        "chain": "ronin",
        "tags": ["bridge", "validator_compromise", "north_korea", "social_engineering"],
    },
    {
        "name": "Poly Network Hack",
        "description": "Cross-chain bridge exploit that took advantage of a contract call vulnerability. $611M stolen initially across Ethereum, Polygon, BSC. Most funds returned after negotiation with hacker ('Mr. White Hat').",
        "year": "2021",
        "amount_usd": 611000000,
        "chain": "ethereum",
        "tags": ["bridge", "cross_chain", "white_hat_returned"],
    },
    {
        "name": "Nomad Bridge Exploit",
        "description": "Smart contract bug in Nomad bridge allowed anyone to drain funds by replaying a legitimate transaction with modified addresses. $190M drained in a chaotic 'crowd-sourced' hack within hours.",
        "year": "2022",
        "amount_usd": 190000000,
        "chain": "ethereum",
        "tags": ["bridge", "replay_attack", "contract_bug"],
    },
]

ENTITY_PROFILES: list[dict[str, Any]] = [
    {
        "name": "Lazarus Group",
        "description": "North Korean state-backed hacking group (APT38, BlueNoroff). Responsible for $3B+ in crypto thefts including Bybit ($1.5B), Ronin ($620M), Harmony Bridge ($100M). Standard tactics: social engineering via LinkedIn, fake job offers, supply chain attacks. Primary money laundering: THORChain, eXch, Sinbad, Wasabi Wallet.",
        "category": "threat_actor",
        "tags": ["north_korea", "state_sponsored", "laundering", "social_engineering"],
    },
    {
        "name": "Inferno Drainer",
        "description": "Phishing-as-a-Service operation that provided pre-built wallet drainer kits to affiliate scammers. Over $80M stolen from 130K+ victims across Ethereum, Polygon, Arbitrum. Operated through Telegram groups. Shut down in late 2024 but newer variants continue.",
        "category": "threat_actor",
        "tags": ["drainer", "phishing", "wallet_drainer", "scam_as_a_service"],
    },
    {
        "name": "Pink Drainer",
        "description": "Sophisticated phishing group operating wallet drainer infrastructure. Over $85M stolen from 21K+ victims. Known for targeting NFT communities and DeFi users via Discord DMs and fake websites. Used social engineering to obtain approvals.",
        "category": "threat_actor",
        "tags": ["drainer", "phishing", "social_engineering", "nft"],
    },
    {
        "name": "MEV Sandwich Bot Networks",
        "description": "Automated MEV extraction operations that front-run and sandwich user transactions on Ethereum, BSC, and Solana. High-tax tokens collude with bot operators to extract maximum value from retail traders. New memecoin launch = immediate sandwich attack.",
        "category": "threat_pattern",
        "tags": ["mev", "sandwich", "frontrunning", "tax_token", "memecoin"],
    },
    {
        "name": "Cross-Chain Bridge Exploit Pattern",
        "description": "Recurring vulnerability class targeting cross-chain bridges. Common vectors: validator set compromise (Ronin, Wormhole), smart contract bugs (Nomad, Poly), liquidity pool manipulation (ThorChain). Over $2.5B lost to bridge hacks total.",
        "category": "vulnerability_pattern",
        "tags": ["bridge", "cross_chain", "validator", "smart_contract"],
    },
    {
        "name": "Token-2022 Metaplex Extension Exploits",
        "description": "New attack surface on Solana's Token-2022 standard. Attackers hide mint authority in Metaplex metadata extensions, create tokens with transfer hooks that drain wallets on transfer, and use 'permanent delegate' extensions for backdoor withdrawals.",
        "category": "vulnerability_pattern",
        "tags": ["solana", "token2022", "metaplex", "transfer_hook", "mint_authority"],
    },
    {
        "name": "Honeypot Token Deployment Pattern",
        "description": "Rapid deployment of honeypot tokens on Solana and Base. Scammers deploy 50-100 tokens per day using pump.fun and similar launchpads. Pattern: liquidity locked in LP but sell function disabled via maxSellAmount=0 or blacklist. Deployer profits from initial buying pressure then rugpulls.",
        "category": "scam_pattern",
        "tags": ["honeypot", "pump_fun", "solana", "base", "rugpull", "rapid_deployment"],
    },
]

CROSS_CHAIN_KNOWN_ADDRESSES = [
    {
        "address": "0x1E0049783F008A0085193E00003D00cd54003c71",
        "label": "Lazarus Group Bybit Hack",
        "chain": "ethereum",
        "severity": "critical",
    },
    {
        "address": "0x47fFc05dC0bA7b1DB5C8A3dE0c5F7d5f3E9e5e5",
        "label": "Inferno Drainer Wallet",
        "chain": "ethereum",
        "severity": "critical",
    },
    {
        "address": "0x000000000000000000000000000000000000dead",
        "label": "Null Address / Burn",
        "chain": "ethereum",
        "severity": "informational",
    },
]


# ── Seeder ─────────────────────────────────────────────────────────────


async def seed_threat_intel(client: httpx.AsyncClient, dry_run: bool) -> int:
    """Seed known scam patterns and historical hacks into threat_intel_v2."""
    count = 0
    texts: list[str] = []
    docs: list[dict[str, Any]] = []

    for pattern in KNOWN_SCAM_PATTERNS:
        text = f"""SCAM PATTERN: {pattern["name"]}
Severity: {pattern["severity"]}
Description: {pattern["description"]}
Indicators: {", ".join(pattern["indicators"])}"""
        doc_id = f"scam_pattern_{pattern['name'].lower().replace(' ', '_').replace('/', '_')}"
        docs.append(
            {
                "doc_id": doc_id,
                "content": text,
                "metadata": {"pattern_name": pattern["name"], "indicators": pattern["indicators"]},
                "source": "curated_patterns",
                "severity": pattern["severity"],
                "chain": "multi",
            }
        )
        texts.append(text)

    for hack in HISTORICAL_HACKS:
        text = f"""HISTORIC HACK: {hack["name"]} ({hack["year"]})
Amount Lost: ${hack["amount_usd"]:,}
Chain: {hack["chain"]}
Description: {hack["description"]}
Tags: {", ".join(hack["tags"])}"""
        doc_id = f"historic_hack_{hack['name'].lower().replace(' ', '_')}"
        docs.append(
            {
                "doc_id": doc_id,
                "content": text,
                "metadata": {
                    "hack_name": hack["name"],
                    "year": hack["year"],
                    "amount_usd": hack["amount_usd"],
                    "tags": hack["tags"],
                },
                "source": "historic_db",
                "severity": "critical" if hack["amount_usd"] >= 100000000 else "high",
                "chain": hack["chain"],
            }
        )
        texts.append(text)

    logger.info("Seeding %d documents into threat_intel_v2...", len(docs))

    if dry_run:
        logger.info("[DRY RUN] Would seed %d docs into threat_intel_v2", len(docs))
        return len(docs)

    await _ensure_collection("threat_intel_v2", client)
    embeddings = await _embed_batch(texts)

    for i, doc in enumerate(docs):
        vec = embeddings[i]
        if vec is None:
            logger.warning("skip %s — embedding failed", doc["doc_id"][:30])
            continue
        ok = await _upsert_point(
            client,
            "threat_intel_v2",
            doc["doc_id"],
            vec,
            content=doc["content"],
            metadata=doc["metadata"],
            source=doc["source"],
            severity=doc["severity"],
            chain=doc["chain"],
        )
        if ok:
            count += 1

    logger.info("threat_intel_v2 seeded: %d/%d docs", count, len(docs))
    return count


async def seed_agent_memory(client: httpx.AsyncClient, dry_run: bool) -> int:
    """Seed entity profiles and cross-chain identities into rmi_agent_memory."""
    count = 0
    texts: list[str] = []
    docs: list[dict[str, Any]] = []

    for entity in ENTITY_PROFILES:
        text = f"""ENTITY PROFILE: {entity["name"]}
Category: {entity["category"]}
Profile: {entity["description"]}
Tags: {", ".join(entity["tags"])}"""
        doc_id = f"entity_{entity['name'].lower().replace(' ', '_')}"
        docs.append(
            {
                "doc_id": doc_id,
                "content": text,
                "metadata": {
                    "entity_name": entity["name"],
                    "category": entity["category"],
                    "tags": entity["tags"],
                },
                "source": "curated_entities",
                "severity": "high",
                "chain": "multi",
            }
        )
        texts.append(text)

    for addr in CROSS_CHAIN_KNOWN_ADDRESSES:
        text = f"""KNOWN ADDRESS: {addr["address"]}
Label: {addr["label"]}
Chain: {addr["chain"]}
Severity: {addr["severity"]}"""
        doc_id = f"known_addr_{addr['chain']}_{addr['address'][:10]}"
        docs.append(
            {
                "doc_id": doc_id,
                "content": text,
                "metadata": {
                    "address": addr["address"],
                    "label": addr["label"],
                    "chain": addr["chain"],
                },
                "source": "curated_addresses",
                "severity": addr["severity"],
                "chain": addr["chain"],
            }
        )
        texts.append(text)

    logger.info("Seeding %d documents into rmi_agent_memory...", len(docs))

    if dry_run:
        logger.info("[DRY RUN] Would seed %d docs into rmi_agent_memory", len(docs))
        return len(docs)

    await _ensure_collection("rmi_agent_memory", client)
    embeddings = await _embed_batch(texts)

    for i, doc in enumerate(docs):
        vec = embeddings[i]
        if vec is None:
            logger.warning("skip %s — embedding failed", doc["doc_id"][:30])
            continue
        ok = await _upsert_point(
            client,
            "rmi_agent_memory",
            doc["doc_id"],
            vec,
            content=doc["content"],
            metadata=doc["metadata"],
            source=doc["source"],
            severity=doc["severity"],
            chain=doc["chain"],
        )
        if ok:
            count += 1

    logger.info("rmi_agent_memory seeded: %d/%d docs", count, len(docs))
    return count


async def seed_community(client: httpx.AsyncClient, dry_run: bool) -> int:
    """Seed community intelligence into rmi_community."""
    community_data: list[dict[str, Any]] = [
        {
            "topic": "Reentrancy Attack Detection",
            "insight": "Most common vulnerability in DeFi protocols. Detection patterns: check for external calls before state changes, check for missing reentrancy guards, check for cross-function reentrancy. Use Slither static analysis + Echidna fuzzing. High-risk patterns: transfer() in withdraw functions, missing nonReentrant modifier.",
            "tags": ["reentrancy", "defi", "solidity", "audit"],
        },
        {
            "topic": "Flash Loan Attack Prevention",
            "insight": "Flash loans enable arbitrage but also enable exploits. Prevention: use Chainlink oracle time-weighted average prices (TWAP), check that borrowed amount + fee is repaid in same tx, implement min/max price bounds, use Balancer-style gradual price updates.",
            "tags": ["flash_loan", "defi", "oracle", "security"],
        },
        {
            "topic": "Solana Token-2022 Risks",
            "insight": "Token-2022 introduces new attack surface. Transfer hooks can execute arbitrary code on every transfer. Permanent delegate authority can withdraw tokens without approval. Always: check for transfer hook extensions, verify mint authority is revoked, check for permanent delegate, use safe indexers that parse Token-2022 correctly.",
            "tags": ["solana", "token2022", "spl", "risk"],
        },
        {
            "topic": "MEV Protection Strategies",
            "insight": "Protect against sandwich attacks: use limit orders with minOut parameters, implement commit-reveal schemes, use Flashbots RPC/MEV-Share on Ethereum, use Jito bundles on Solana. For high-value trades: split across multiple DEXes, use private mempools, set slippage tolerance <0.5%.",
            "tags": ["mev", "sandwich", "protection", "defi"],
        },
        {
            "topic": "Cross-Chain Bridge Security Best Practices",
            "insight": "Bridge exploits have caused $2.5B+ in losses. Security checklist: use threshold signatures (not multi-sig), implement rate limiting per validator, use optimistic verification with challenge period, audit cross-chain message passing thoroughly, monitor for abnormal validator set changes, implement circuit breakers for large transfers.",
            "tags": ["bridge", "cross_chain", "security", "best_practices"],
        },
    ]

    count = 0
    texts: list[str] = []
    docs: list[dict[str, Any]] = []

    for item in community_data:
        text = f"""COMMUNITY TOPIC: {item["topic"]}
Insight: {item["insight"]}
Tags: {", ".join(item["tags"])}"""
        doc_id = f"community_{item['topic'].lower().replace(' ', '_')}"
        docs.append(
            {
                "doc_id": doc_id,
                "content": text,
                "metadata": {"topic": item["topic"], "tags": item["tags"]},
                "source": "curated_community",
                "severity": "medium",
                "chain": "multi",
            }
        )
        texts.append(text)

    logger.info("Seeding %d documents into rmi_community...", len(docs))

    if dry_run:
        logger.info("[DRY RUN] Would seed %d docs into rmi_community", len(docs))
        return len(docs)

    await _ensure_collection("rmi_community", client)
    embeddings = await _embed_batch(texts)

    for i, doc in enumerate(docs):
        vec = embeddings[i]
        if vec is None:
            logger.warning("skip %s — embedding failed", doc["doc_id"][:30])
            continue
        ok = await _upsert_point(
            client,
            "rmi_community",
            doc["doc_id"],
            vec,
            content=doc["content"],
            metadata=doc["metadata"],
            source=doc["source"],
            severity=doc["severity"],
            chain=doc["chain"],
        )
        if ok:
            count += 1

    logger.info("rmi_community seeded: %d/%d docs", count, len(docs))
    return count


async def try_fetch_backend_scans(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Attempt to fetch recent scanner findings from rmi-backend."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as bc:
            r = await bc.get(f"{BACKEND_URL}/api/v1/scanner/recent", params={"limit": 50})
            if r.status_code == 200:
                data = r.json()
                findings = (
                    data
                    if isinstance(data, list)
                    else data.get("findings", data.get("results", []))
                )
                logger.info("Fetched %d recent scanner findings from backend", len(findings))
                return findings
            else:
                logger.info("Backend scanner endpoint returned %s (expected)", r.status_code)
    except Exception as e:
        logger.info("Backend scan fetch skipped (expected if backend unavailable): %s", e)
    return []


async def seed_backend_findings(client: httpx.AsyncClient, findings: list[dict[str, Any]]) -> int:
    """Embed and upsert backend scanner findings into threat_intel_v2."""
    if not findings:
        return 0

    count = 0
    texts: list[str] = []
    docs: list[dict[str, Any]] = []

    for finding in findings:
        token_address = finding.get("token_address", finding.get("address", "unknown"))
        chain = finding.get("chain", "unknown")
        risk_level = finding.get("risk_level", finding.get("safety", "unknown"))
        name = finding.get("name", "")
        symbol = finding.get("symbol", "")
        risk_flags: list[str] = finding.get("risk_flags", finding.get("flags", []))

        text = f"""SCAM ALERT [{chain.upper()}] {name} ({symbol}) - {token_address}
Risk Level: {risk_level.upper()} | Safety: medium
Risk Flags: {", ".join(risk_flags[:15]) if risk_flags else "None detected"}"""
        doc_id = f"scanner_{chain}_{token_address}".replace(":", "_").lower()
        docs.append(
            {
                "doc_id": doc_id,
                "content": text,
                "metadata": {
                    "token_address": token_address,
                    "chain": chain,
                    "name": name,
                    "symbol": symbol,
                },
                "source": "scanner",
                "severity": risk_level,
                "chain": chain,
            }
        )
        texts.append(text)

    embeddings = await _embed_batch(texts)
    for i, doc in enumerate(docs):
        vec = embeddings[i]
        if vec is None:
            continue
        ok = await _upsert_point(
            client,
            "threat_intel_v2",
            doc["doc_id"],
            vec,
            doc["content"],
            doc["metadata"],
            doc["source"],
            doc["severity"],
            doc["chain"],
        )
        if ok:
            count += 1

    logger.info("Backend findings seeded: %d/%d", count, len(docs))
    return count


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed RAG collections")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be seeded")
    parser.add_argument(
        "--skip-backend", action="store_true", help="Skip fetching backend scanner findings"
    )
    args = parser.parse_args()

    logger.info("RAG Seeder starting (dry_run=%s)", args.dry_run)

    totals: dict[str, Any] = {}

    async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30.0) as client:
        # 1. Seed threat_intel_v2
        totals["threat_intel_v2"] = await seed_threat_intel(client, args.dry_run)

        # 2. Seed rmi_agent_memory
        totals["rmi_agent_memory"] = await seed_agent_memory(client, args.dry_run)

        # 3. Seed rmi_community
        totals["rmi_community"] = await seed_community(client, args.dry_run)

        # 4. Fetch and seed backend findings (non-blocking)
        if not args.skip_backend and not args.dry_run:
            findings = await try_fetch_backend_scans(client)
            if findings:
                totals["backend_scans"] = await seed_backend_findings(client, findings)
        else:
            logger.info(
                "Skipping backend scanner fetch (skip_backend=%s or dry_run=%s)",
                args.skip_backend,
                args.dry_run,
            )

    total = sum(totals.values())
    logger.info("=" * 60)
    logger.info("SEED COMPLETE")
    logger.info("  threat_intel_v2: %d docs", totals.get("threat_intel_v2", 0))
    logger.info("  rmi_agent_memory:  %d docs", totals.get("rmi_agent_memory", 0))
    logger.info("  rmi_community:    %d docs", totals.get("rmi_community", 0))
    if "backend_scans" in totals:
        logger.info("  backend_scans:    %d docs", totals["backend_scans"])
    logger.info("  TOTAL: %d docs across %d collections", total, len(totals))
    logger.info("=" * 60)

    # Print JSON summary
    result = {
        "status": "complete" if not args.dry_run else "dry_run",
        "collections_seeded": {
            "threat_intel_v2": totals.get("threat_intel_v2", 0),
            "rmi_agent_memory": totals.get("rmi_agent_memory", 0),
            "rmi_community": totals.get("rmi_community", 0),
        },
        "total_docs": total,
        "embedding_model": QWEN_EMBED_MODEL_NAME,
        "embedding_dim": EMBEDDING_DIM,
    }
    if "backend_scans" in totals:
        result["collections_seeded"]["backend_scans"] = totals["backend_scans"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
