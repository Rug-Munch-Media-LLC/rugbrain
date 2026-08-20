#!/usr/bin/env python3
"""
Multi-Chain Scam Address Ingestion
===================================
Aggregate blockchain scam addresses from multiple sources into RAG known_scams.

Sources (address-level, chain-specific):
  1. Etherscan phish-hack.csv        - 110 phishing/hack addresses (ETH/BSC/Polygon/etc)
  2. Etherscan combined_labels.csv    - 3,472 scam-labeled addresses across 7 chains
  3. Etherscan combined_labels.csv    - 46K+ labeled wallets → wallet_profiles collection
  4. Solana token-registry JSON        - 55 explicitly flagged scam tokens (Solana)
  5. Rekt hack database (JSON)         - DeFi exploit addresses (ETH/BSC/etc)
  6. MetaMask phishing domains         - 198K phishing domains (domain-level, batched)

Strategy:
  - ADDRESS-LEVEL data goes 1:1 into known_scams (high value for our scanners)
  - DOMAIN-LEVEL data gets batched 50/domain per doc (supplementary, lower value)
  - Wallet profile data goes to wallet_profiles collection (non-scam labels)

This replaces the old single-source scripts with a unified approach.
"""

import asyncio
import csv
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_multi_chain_scams")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAG_API = "http://localhost:8000/api/v1/rag/ingest"

SCAM_LABEL_KEYWORDS = [
    "phish",
    "hack",
    "exploit",
    "scam",
    "drain",
    "attack",
    "heist",
    "malicious",
    "fraud",
    "rug",
    "compromis",
    "whale-alert",
]


def make_doc_id(collection: str, key: str) -> str:
    return hashlib.sha256(f"{collection}:{key.lower()}".encode()).hexdigest()[:16]


async def ingest_doc(collection: str, content: str, metadata: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                RAG_API,
                json={
                    "collection": collection,
                    "content": content,
                    "metadata": metadata,
                },
            )
            return resp.status_code == 200
    except Exception as e:
        logger.debug(f"Ingest error: {e}")
        return False


# ─── Source 1: Etherscan Phish/Hack CSV ─────────────────────────────


async def ingest_etherscan_phish_hack() -> dict[str, int]:
    csv_path = os.path.join(DATA_DIR, "etherscan_phish_hack.csv")
    if not Path(csv_path).exists():  # noqa: ASYNC240
        logger.warning(f"Missing: {csv_path}")
        return {"ingested": 0, "errors": 0}

    ingested = 0
    errors = 0
    with open(csv_path, newline="", encoding="utf-8") as f:  # noqa: ASYNC230
        rows = list(csv.DictReader(f))

    logger.info(f"Etherscan phish/hack: {len(rows)} addresses")
    for i, row in enumerate(rows):
        address = row["address"].strip()
        if not address:
            continue
        name_tag = row.get("name_tag", "").strip()
        label_type = row.get("label_type", "phish-hack").strip()
        chain = row.get("chain", "Ethereum").strip()
        balance = row.get("balance", "").strip()
        txn_count = row.get("txn_count", "").strip()

        doc_id = make_doc_id("known_scams", f"eth_phish:{address}:{chain}")
        content = (
            f"Etherscan {label_type} label: {address} on {chain}. "
            f"Tag: {name_tag}. Balance: {balance}. Txns: {txn_count}."
        )
        metadata = {
            "address": address.lower(),
            "label_type": label_type,
            "name_tag": name_tag,
            "chain": chain.lower(),
            "balance": balance,
            "txn_count": txn_count,
            "source": "etherscan_phish_hack",
            "severity": "critical" if "phish" in label_type.lower() else "high",
            "doc_id": doc_id,
        }

        if await ingest_doc("known_scams", content, metadata):
            ingested += 1
        else:
            errors += 1

        if (i + 1) % 20 == 0:
            logger.info(f"  Phish/hack: {i + 1}/{len(rows)} | ingested: {ingested}")

    return {"ingested": ingested, "errors": errors}


# ─── Source 2: Etherscan Combined Labels CSV ─────────────────────────


async def ingest_etherscan_combined() -> dict[str, int]:
    csv_path = os.path.join(DATA_DIR, "etherscan_combined_labels.csv")
    if not Path(csv_path).exists():  # noqa: ASYNC240
        logger.warning(f"Missing: {csv_path}")
        return {"known_scams": 0, "wallet_profiles": 0, "errors": 0}

    stats = {"known_scams": 0, "wallet_profiles": 0, "errors": 0}
    with open(csv_path, newline="", encoding="utf-8") as f:  # noqa: ASYNC230
        rows = list(csv.DictReader(f))

    scam_rows = [
        r for r in rows if any(kw in r.get("label", "").lower() for kw in SCAM_LABEL_KEYWORDS)
    ]
    wallet_rows = [
        r for r in rows if not any(kw in r.get("label", "").lower() for kw in SCAM_LABEL_KEYWORDS)
    ]

    logger.info(
        f"Etherscan combined: {len(rows)} total → {len(scam_rows)} scam → known_scams, {len(wallet_rows)} → wallet_profiles"
    )

    for i, row in enumerate(rows):
        address = row["address"].strip()
        name = row.get("name", "").strip()
        label = row.get("label", "").strip()
        chain = row.get("chain", "Ethereum").strip()
        if not address or not label:
            continue

        is_scam = any(kw in label.lower() for kw in SCAM_LABEL_KEYWORDS)

        if is_scam:
            collection = "known_scams"
            doc_id = make_doc_id("known_scams", f"eth_combined:{address}:{chain}")
            content = (
                f"Etherscan scam label: {address} ({name}) on {chain}. "
                f"Category: {label}. Flagged as scam/phishing/exploit address."
            )
            metadata = {
                "address": address.lower(),
                "label": label,
                "name": name,
                "chain": chain.lower(),
                "source": "etherscan_combined",
                "severity": "high",
                "label_type": "scam",
                "doc_id": doc_id,
            }
        else:
            collection = "wallet_profiles"
            doc_id = make_doc_id("wallet_profiles", f"eth_combined:{address}:{chain}")
            content = (
                f"Etherscan labeled address: {address} ({name}) on {chain}. Category: {label}."
            )
            metadata = {
                "address": address.lower(),
                "label": label,
                "name": name,
                "chain": chain.lower(),
                "source": "etherscan_combined",
                "label_type": "profile",
                "doc_id": doc_id,
            }

        if await ingest_doc(collection, content, metadata):
            stats[collection] += 1
        else:
            stats["errors"] += 1

        if (i + 1) % 1000 == 0:
            logger.info(
                f"  Combined: {i + 1}/{len(rows)} | scams: {stats['known_scams']}, profiles: {stats['wallet_profiles']}, errors: {stats['errors']}"
            )
        if (i + 1) % 500 == 0:
            await asyncio.sleep(0)

    return stats


# ─── Source 3: Rekt Hack Database ────────────────────────────────────


async def ingest_rekt_hacks() -> dict[str, int]:
    json_path = os.path.join(DATA_DIR, "rekt_hacks.json")
    if not Path(json_path).exists():  # noqa: ASYNC240
        # Try raw
        json_path = os.path.join(DATA_DIR, "rekt_hacks_raw.json")
    if not Path(json_path).exists():  # noqa: ASYNC240
        logger.warning("Missing rekt_hacks JSON")
        return {"ingested": 0, "errors": 0}

    with open(json_path) as f:  # noqa: ASYNC230
        hacks = json.load(f)

    if isinstance(hacks, dict):
        hacks = hacks.get("hacks", hacks.get("data", []))
    if not isinstance(hacks, list):
        logger.warning(f"Unexpected rekt format: {type(hacks)}")
        return {"ingested": 0, "errors": 0}

    logger.info(f"Rekt hacks: {len(hacks)} entries")
    ingested = 0
    errors = 0

    for i, hack in enumerate(hacks):
        if not isinstance(hack, dict):
            continue
        name = hack.get("project", hack.get("name", ""))
        chain = hack.get("chain", "ethereum")
        attacker = hack.get("attacker_address", hack.get("attacker", ""))
        contract = hack.get("contract_address", hack.get("contract", ""))
        amount = hack.get("amount", hack.get("funds_lost", ""))
        attack_type = hack.get("attack_type", hack.get("type", ""))
        description = hack.get("description", "")
        date = hack.get("date", "")

        # Ingest attacker address
        if attacker and len(str(attacker)) > 10:
            doc_id = make_doc_id("known_scams", f"rekt_attacker:{attacker}")
            content = (
                f"DeFi hack attacker address: {attacker} on {chain}. "
                f"Project: {name}. Loss: ${amount}. Type: {attack_type}. "
                f"Date: {date}. {description[:200]}"
            )
            metadata = {
                "address": str(attacker).lower(),
                "chain": chain.lower(),
                "source": "rekt_hacks",
                "name": name,
                "attack_type": attack_type,
                "amount": str(amount),
                "severity": "critical",
                "doc_id": doc_id,
            }
            if await ingest_doc("known_scams", content, metadata):
                ingested += 1
            else:
                errors += 1

        # Ingest exploited contract
        if contract and len(str(contract)) > 10 and str(contract) != str(attacker):
            doc_id = make_doc_id("known_scams", f"rekt_contract:{contract}")
            content = (
                f"DeFi hack exploited contract: {contract} on {chain}. "
                f"Project: {name}. Loss: ${amount}. Type: {attack_type}. "
                f"Date: {date}. {description[:200]}"
            )
            metadata = {
                "address": str(contract).lower(),
                "chain": chain.lower(),
                "source": "rekt_hacks",
                "name": name,
                "attack_type": attack_type,
                "amount": str(amount),
                "severity": "high",
                "doc_id": doc_id,
            }
            if await ingest_doc("known_scams", content, metadata):
                ingested += 1
            else:
                errors += 1

        if (i + 1) % 50 == 0:
            logger.info(f"  Rekt: {i + 1}/{len(hacks)} | ingested: {ingested}")

    return {"ingested": ingested, "errors": errors}


# ─── Source 4: Solana Token Registry (flagged scams) ──────────────────


async def ingest_solana_flagged_tokens() -> dict[str, int]:
    """Download Solana token list and ingest explicitly flagged scam tokens."""
    token_list_url = "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"  # noqa: S105 — config default

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(token_list_url)
            if resp.status_code != 200:
                logger.error(f"Solana token list fetch failed: {resp.status_code}")
                return {"ingested": 0, "errors": 0, "fetch_ok": False}
            data = resp.json()
    except Exception as e:
        logger.error(f"Solana token list error: {e}")
        return {"ingested": 0, "errors": 0, "fetch_ok": False}

    tokens = data.get("tokens", [])
    # Filter for scam/spam/suspicious flags in name, symbol, or tags
    scam_keywords = ["scam", "spam", "phishing", "fake", "fraud", "rug", "exploit"]
    flagged = []
    for t in tokens:
        name_lower = (t.get("name", "") + " " + t.get("symbol", "")).lower()
        tags_lower = [str(tag).lower() for tag in t.get("tags", [])]
        if any(kw in name_lower for kw in scam_keywords) or any(
            kw in " ".join(tags_lower) for kw in scam_keywords
        ):
            flagged.append(t)

    logger.info(f"Solana token list: {len(tokens)} total, {len(flagged)} flagged as scam/spam")
    ingested = 0
    errors = 0

    for t in flagged:
        address = t.get("address", "")
        name = t.get("name", "")
        symbol = t.get("symbol", "")
        if not address or len(address) < 20:
            continue

        doc_id = make_doc_id("known_scams", f"sol_flagged:{address}")
        content = (
            f"Solana flagged scam token: {name} ({symbol}). "
            f"Mint address: {address}. Flagged in Solana token registry as scam/spam/phishing."
        )
        metadata = {
            "address": address,
            "chain": "solana",
            "source": "solana_token_registry",
            "name": name,
            "symbol": symbol,
            "severity": "high",
            "category": "scam_token",
            "doc_id": doc_id,
        }
        if await ingest_doc("known_scams", content, metadata):
            ingested += 1
        else:
            errors += 1

    return {"ingested": ingested, "errors": errors, "fetch_ok": True}


# ─── Source 5: MetaMask Phishing Domains (batched, supplementary) ────


async def ingest_metamask_domains() -> dict[str, int]:
    """Ingest MetaMask phishing domain blacklist in batches of 50."""
    config_url = (
        "https://raw.githubusercontent.com/MetaMask/eth-phishing-detect/master/src/config.json"
    )

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(config_url)
            if resp.status_code != 200:
                logger.error(f"MetaMask list fetch failed: {resp.status_code}")
                return {"ingested": 0, "errors": 0, "fetch_ok": False}
            data = resp.json()
    except Exception as e:
        logger.error(f"MetaMask list error: {e}")
        return {"ingested": 0, "errors": 0, "fetch_ok": False}

    blacklist = data.get("blacklist", [])
    logger.info(f"MetaMask blacklist: {len(blacklist)} domains (batching 50/doc)")

    batch_size = 50
    total_batches = (len(blacklist) + batch_size - 1) // batch_size
    ingested = 0
    errors = 0

    for batch_idx in range(total_batches):
        batch = blacklist[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        domain_list = "\n".join(batch)
        doc_id = make_doc_id("known_scams", f"mm_batch:{batch_idx}")
        content = (
            f"MetaMask Confirmed Phishing Domains (Batch {batch_idx + 1}/{total_batches}):\n"
            f"{domain_list}\n"
            f"Source: MetaMask eth-phishing-detect. Confirmed phishing/scam websites targeting crypto users."
        )
        metadata = {
            "source": "metamask_phishing_detect",
            "batch_index": batch_idx,
            "total_batches": total_batches,
            "domain_count": len(batch),
            "category": "phishing_domain",
            "severity": "medium",
            "doc_id": doc_id,
        }

        if await ingest_doc("known_scams", content, metadata):
            ingested += 1
        else:
            errors += 1

        if (batch_idx + 1) % 100 == 0:
            logger.info(
                f"  MetaMask: {batch_idx + 1}/{total_batches} batches | ingested: {ingested}"
            )
            await asyncio.sleep(0.05)

    return {"ingested": ingested, "errors": errors, "fetch_ok": True}


# ─── Source 6: ScamSniffer GitHub Repo (phishing domains + addresses) ────


async def ingest_scamsniffer_github() -> dict[str, int]:
    """Ingest ScamSniffer GitHub scam-database repo.

    Contains blacklists of phishing domains and malicious wallet addresses.
    7-day delay vs real-time but excellent for historical pattern analysis.
    """
    base_url = "https://raw.githubusercontent.com/scamsniffer/scam-database/main"
    # Current upstream layout (verified 2026-08): blacklist/ contains domains.json,
    # address.json (singular), combined.json + all.json. The old addresses.json /
    # urls.json files were removed upstream — address.json replaced addresses.json
    # and combined.json (domain -> [addresses]) replaced urls.json.
    files_to_fetch = [
        "blacklist/domains.json",  # list of phishing domain strings
        "blacklist/address.json",  # list of malicious wallet address strings
        "blacklist/combined.json",  # dict: phishing domain -> [wallet addresses]
    ]

    ingested = 0
    errors = 0
    total_domains = 0
    total_addresses = 0
    fetch_failed = False

    for file_path in files_to_fetch:
        url = f"{base_url}/{file_path}"
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.error(f"ScamSniffer {file_path}: HTTP {resp.status_code}")
                    fetch_failed = True
                    continue
                data = resp.json()
        except Exception as e:
            logger.error(f"ScamSniffer {file_path} error: {e}")
            fetch_failed = True
            continue

        item_type = file_path.split("/")[-1].replace(".json", "")
        if isinstance(data, dict) and item_type == "combined":
            # combined.json: domain -> [addresses]; flatten into display strings
            data = [f"{domain}: {', '.join(addrs)}" for domain, addrs in data.items()]

        if not isinstance(data, list):
            logger.error(f"ScamSniffer {file_path}: unexpected format {type(data)}")
            fetch_failed = True
            continue

        logger.info(f"ScamSniffer {item_type}: {len(data)} entries")

        # Batch ingest
        batch_size = 50
        total_batches = (len(data) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            batch = data[batch_idx * batch_size : (batch_idx + 1) * batch_size]

            if item_type == "domains":
                content = (
                    f"ScamSniffer Confirmed Phishing Domains (Batch {batch_idx + 1}/{total_batches}):\n"
                    f"{chr(10).join(batch)}\n"
                    f"Source: ScamSniffer scam-database GitHub. 7-day delayed but verified."
                )
                metadata = {
                    "source": "scamsniffer_github",
                    "item_type": "domain",
                    "batch_index": batch_idx,
                    "total_batches": total_batches,
                    "item_count": len(batch),
                    "category": "phishing_domain",
                    "severity": "high",
                }
                total_domains += len(batch)
            elif item_type == "addresses":
                # Addresses may be dicts or strings
                addr_list = []
                for item in batch:
                    if isinstance(item, dict):
                        addr = item.get("address", item.get("addr", ""))
                        chain = item.get("chain", "unknown")
                        label = item.get("label", "")
                        if addr:
                            addr_list.append(f"{addr} ({chain}) {label}")
                    elif isinstance(item, str):
                        addr_list.append(item)

                content = (
                    f"ScamSniffer Malicious Addresses (Batch {batch_idx + 1}/{total_batches}):\n"
                    f"{chr(10).join(addr_list)}\n"
                    f"Source: ScamSniffer scam-database GitHub. Wallet drainer and phishing addresses."
                )
                metadata = {
                    "source": "scamsniffer_github",
                    "item_type": "address",
                    "batch_index": batch_idx,
                    "total_batches": total_batches,
                    "item_count": len(batch),
                    "category": "malicious_address",
                    "severity": "critical",
                }
                total_addresses += len(batch)
            else:  # combined (replaces retired urls.json: domain -> [addresses])
                content = (
                    f"ScamSniffer Phishing Domain Address Mappings (Batch {batch_idx + 1}/{total_batches}):\n"
                    f"{chr(10).join(batch)}\n"
                    f"Source: ScamSniffer scam-database GitHub. Verified phishing domains and the drainer "
                    f"addresses they are linked to."
                )
                metadata = {
                    "source": "scamsniffer_github",
                    "item_type": "combined",
                    "batch_index": batch_idx,
                    "total_batches": total_batches,
                    "item_count": len(batch),
                    "category": "malicious_domain_address",
                    "severity": "high",
                }

            if await ingest_doc("known_scams", content, metadata):
                ingested += 1
            else:
                errors += 1

            if (batch_idx + 1) % 50 == 0:
                logger.info(
                    f"  ScamSniffer {item_type}: {batch_idx + 1}/{total_batches} batches | ingested: {ingested}"
                )
                await asyncio.sleep(0.05)

    logger.info(f"ScamSniffer total: {total_domains} domains, {total_addresses} addresses")
    return {
        "ingested": ingested,
        "errors": errors,
        "domains": total_domains,
        "addresses": total_addresses,
        "fetch_ok": not fetch_failed,
    }


# ─── Source 7: PhishDestroy Destroylist (140K+ threats) ────


async def ingest_phishdestroy() -> dict[str, int]:
    """Ingest PhishDestroy destroylist - 140K curated crypto/web3 scam domains.

    MIT licensed. Available as list.txt / list.json (the old active_domains.json /
    domains.txt files were renamed upstream — verified 2026-08). We fetch the
    plain text list and batch it.
    """
    urls = {
        "domains_txt": "https://raw.githubusercontent.com/PhishDestroy/destroylist/main/list.txt",
        "active_json": "https://raw.githubusercontent.com/PhishDestroy/destroylist/main/list.json",
    }

    ingested = 0
    errors = 0
    total_domains = 0
    fetch_ok = False

    # Try list.json first (structured, ~190K domains)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(urls["active_json"])
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    domains = data
                elif isinstance(data, dict):
                    domains = list(data.keys())
                else:
                    domains = []

                if domains:
                    fetch_ok = True
                logger.info(f"PhishDestroy list.json: {len(domains)} domains")

                batch_size = 50
                total_batches = (len(domains) + batch_size - 1) // batch_size

                for batch_idx in range(total_batches):
                    batch = domains[batch_idx * batch_size : (batch_idx + 1) * batch_size]
                    domain_list = "\n".join(batch)
                    doc_id = make_doc_id("known_scams", f"pd_active:{batch_idx}")
                    content = (
                        f"PhishDestroy Active Malicious Domains (Batch {batch_idx + 1}/{total_batches}):\n"
                        f"{domain_list}\n"
                        f"Source: PhishDestroy destroylist (MIT License). DNS-validated active crypto/web3 scam domains."
                    )
                    metadata = {
                        "source": "phishdestroy_destroylist",
                        "batch_index": batch_idx,
                        "total_batches": total_batches,
                        "domain_count": len(batch),
                        "category": "phishing_domain",
                        "severity": "high",
                        "license": "MIT",
                        "doc_id": doc_id,
                    }

                    if await ingest_doc("known_scams", content, metadata):
                        ingested += 1
                        total_domains += len(batch)
                    else:
                        errors += 1

                    if (batch_idx + 1) % 100 == 0:
                        logger.info(
                            f"  PhishDestroy: {batch_idx + 1}/{total_batches} | ingested: {ingested}"
                        )
                        await asyncio.sleep(0.05)
    except Exception as e:
        logger.error(f"PhishDestroy list.json failed: {e}")

    # Fallback to list.txt
    if total_domains == 0:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(urls["domains_txt"])
                if resp.status_code == 200:
                    domains = [d.strip() for d in resp.text.splitlines() if d.strip()]
                    if domains:
                        fetch_ok = True
                    logger.info(f"PhishDestroy list.txt: {len(domains)} domains")

                    batch_size = 50
                    total_batches = (len(domains) + batch_size - 1) // batch_size

                    for batch_idx in range(total_batches):
                        batch = domains[batch_idx * batch_size : (batch_idx + 1) * batch_size]
                        domain_list = "\n".join(batch)
                        doc_id = make_doc_id("known_scams", f"pd_txt:{batch_idx}")
                        content = (
                            f"PhishDestroy Malicious Domains (Batch {batch_idx + 1}/{total_batches}):\n"
                            f"{domain_list}\n"
                            f"Source: PhishDestroy destroylist (MIT License)."
                        )
                        metadata = {
                            "source": "phishdestroy_destroylist",
                            "batch_index": batch_idx,
                            "total_batches": total_batches,
                            "domain_count": len(batch),
                            "category": "phishing_domain",
                            "severity": "high",
                            "license": "MIT",
                            "doc_id": doc_id,
                        }

                        if await ingest_doc("known_scams", content, metadata):
                            ingested += 1
                            total_domains += len(batch)
                        else:
                            errors += 1

                        if (batch_idx + 1) % 100 == 0:
                            logger.info(
                                f"  PhishDestroy: {batch_idx + 1}/{total_batches} | ingested: {ingested}"
                            )
                            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"PhishDestroy list.txt failed: {e}")

    if not fetch_ok:
        logger.error("PhishDestroy: all upstream files failed to fetch")
    logger.info(f"PhishDestroy total: {total_domains} domains ingested")
    return {"ingested": ingested, "errors": errors, "domains": total_domains, "fetch_ok": fetch_ok}


# ─── Main ─────────────────────────────────────────────────────────────


async def main():
    total_stats = {
        "known_scams_added": 0,
        "wallet_profiles_added": 0,
        "errors": 0,
        "sources": {},
    }

    # Source 1: Etherscan phish/hack (110 addresses, HIGH VALUE)
    logger.info("=" * 60)
    logger.info("SOURCE 1: Etherscan Phish/Hack Addresses")
    logger.info("=" * 60)
    r = await ingest_etherscan_phish_hack()
    total_stats["known_scams_added"] += r["ingested"]
    total_stats["errors"] += r["errors"]
    total_stats["sources"]["etherscan_phish_hack"] = r
    logger.info(f"  Result: {r}")

    # Source 2: Etherscan combined labels (3,472 scam + 46K profiles)
    logger.info("=" * 60)
    logger.info("SOURCE 2: Etherscan Combined Labels")
    logger.info("=" * 60)
    r = await ingest_etherscan_combined()
    total_stats["known_scams_added"] += r["known_scams"]
    total_stats["wallet_profiles_added"] += r["wallet_profiles"]
    total_stats["errors"] += r["errors"]
    total_stats["sources"]["etherscan_combined"] = r
    logger.info(f"  Result: {r}")

    # Source 3: Rekt hack database (attacker + exploited contract addresses)
    logger.info("=" * 60)
    logger.info("SOURCE 3: Rekt DeFi Hack Database")
    logger.info("=" * 60)
    r = await ingest_rekt_hacks()
    total_stats["known_scams_added"] += r["ingested"]
    total_stats["errors"] += r["errors"]
    total_stats["sources"]["rekt_hacks"] = r
    logger.info(f"  Result: {r}")

    # Source 4: Solana flagged scam tokens
    logger.info("=" * 60)
    logger.info("SOURCE 4: Solana Token Registry Flagged Scams")
    logger.info("=" * 60)
    r = await ingest_solana_flagged_tokens()
    total_stats["known_scams_added"] += r["ingested"]
    total_stats["errors"] += r["errors"]
    total_stats["sources"]["solana_flagged"] = r
    logger.info(f"  Result: {r}")

    # Source 5: MetaMask phishing domains (supplementary, low priority)
    logger.info("=" * 60)
    logger.info("SOURCE 5: MetaMask Phishing Domain Blacklist")
    logger.info("=" * 60)
    r = await ingest_metamask_domains()
    total_stats["known_scams_added"] += r["ingested"]
    total_stats["errors"] += r["errors"]
    total_stats["sources"]["metamask_domains"] = r
    logger.info(f"  Result: {r}")

    # Source 6: ScamSniffer GitHub repo (phishing domains + addresses)
    logger.info("=" * 60)
    logger.info("SOURCE 6: ScamSniffer GitHub Scam Database")
    logger.info("=" * 60)
    r = await ingest_scamsniffer_github()
    total_stats["known_scams_added"] += r["ingested"]
    total_stats["errors"] += r["errors"]
    total_stats["sources"]["scamsniffer_github"] = r
    logger.info(f"  Result: {r}")

    # Source 7: PhishDestroy destroylist (140K+ curated threats)
    logger.info("=" * 60)
    logger.info("SOURCE 7: PhishDestroy Destroylist")
    logger.info("=" * 60)
    r = await ingest_phishdestroy()
    total_stats["known_scams_added"] += r["ingested"]
    total_stats["errors"] += r["errors"]
    total_stats["sources"]["phishdestroy"] = r
    logger.info(f"  Result: {r}")

    logger.info("=" * 60)
    logger.info(f"FINAL SUMMARY: {total_stats}")
    logger.info("=" * 60)

    # Fail loud: any upstream (network) source that could not be fetched must
    # surface as a non-zero exit so scanner-db-sync.sh reports a failure instead
    # of silently recording failed=0 with 0 records ingested.
    fetch_failures = [
        name
        for name, result in total_stats["sources"].items()
        if isinstance(result, dict) and result.get("fetch_ok") is False
    ]
    if fetch_failures:
        logger.error(f"FATAL: upstream fetch failed for source(s): {', '.join(fetch_failures)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
