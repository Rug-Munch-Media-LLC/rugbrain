#!/usr/bin/env python3
"""Ingest Kaggle scam/fraud datasets into RugBrain scam collections.

Sources (all staged under /srv/data/kaggle, never committed):
  1. johnny1994/all-nft-rugpulls-and-scams        — 327 named rug pulls / scams
  2. zongaobian/cryptocurrency-scam-dataset       — 9,889 phishing/scam URLs + addresses
  3. sergionefedov/crypto-exchange-fraud-and-wash-trading-detection
                                                  — wallets + manipulative transactions
  4. vagifa/ethereum-frauddetection-dataset       — 9,841 labelled ETH accounts
     (already staged as parquet at /srv/data/lake/ethereum_fraud/transaction_dataset.parquet)

Target collections (live 4096-dim *_8b lane):
  * ``scam_intel_v2_8b`` — scam entities, phishing URLs, fraud wallets, rug pulls
  * ``known_scams_8b``   — documented scam registry (named scams, flagged addresses)

Each entity is embedded (4096-dim Qwen3-Embedding-8B via the DeepInfra lane) and
upserted with canonical metadata. Point IDs are deterministic
(``kaggle_scam:{source}:{kind}:{key}``) so re-runs are idempotent.

Usage:
    python scripts/ingest_kaggle_scam_fraud.py --dry-run
    python scripts/ingest_kaggle_scam_fraud.py --limit 500
    python scripts/ingest_kaggle_scam_fraud.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - DEEPINFRA_API_KEY for the 8B lane
  - pandas + pyarrow (system python3) for the parquet lake
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rugbrain.embedder import get_embedder
from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_kaggle_scam_fraud")

KAGGLE_DIR = Path(os.getenv("KAGGLE_STAGE_DIR", "/srv/data/kaggle"))
LAKE_DIR = Path(os.getenv("LAKE_DIR", "/srv/data/lake"))

SCAM_COLLECTION = "scam_intel_v2_8b"
KNOWN_SCAMS_COLLECTION = "known_scams_8b"

SOURCE = "kaggle_scam_fraud"
BATCH_SIZE = 256


def _doc(
    doc_id: str,
    text: str,
    metadata: dict[str, Any],
    collection: str,
    severity: str = "high",
    chain: str = "multi",
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "text": text,
        "metadata": metadata,
        "collection": collection,
        "severity": severity,
        "chain": chain,
    }


# ── Source 1: NFT rug pulls / named scams ────────────────────────────────────
def _nft_rugpull_docs(limit: int | None) -> list[dict[str, Any]]:
    import pandas as pd

    path = KAGGLE_DIR / "johnny1994_all-nft-rugpulls-and-scams" / "NFT Rug Pulls.csv"
    if not path.exists():
        logger.warning("nft_rugpull_missing path=%s", path)
        return []
    df = pd.read_csv(path)
    docs: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        name = str(row.get("Company Name", "")).strip()
        if not name or name.lower() == "nan":
            continue
        kind = str(row.get("Rug Pull vs Scam", "")).strip()
        sector = str(row.get("Crypto vs NFT", "")).strip()
        date = str(row.get("Date", "")).strip()
        amount = row.get("Amount Stolen", "")
        try:
            amount_f = float(amount)
        except (TypeError, ValueError):
            amount_f = 0.0
        text = (
            f"Named crypto scam / rug pull: {name}\n"
            f"Type: {kind}\nSector: {sector}\nDate: {date}\n"
            f"Amount stolen (USD): {amount_f:,.0f}\n"
            f"Source: Kaggle all-nft-rugpulls-and-scams"
        )
        docs.append(
            _doc(
                doc_id=f"{SOURCE}:rugpull:{name.lower()}",
                text=text,
                metadata={
                    "entity": name,
                    "entity_type": "named_scam",
                    "scam_type": kind,
                    "sector": sector,
                    "date": date,
                    "amount_stolen_usd": amount_f,
                    "source": SOURCE,
                    "source_type": "kaggle_nft_rugpulls",
                },
                collection=KNOWN_SCAMS_COLLECTION,
                severity="critical",
            )
        )
        if limit and len(docs) >= limit:
            break
    return docs


# ── Source 2: CryptoScamDB phishing URLs + addresses ─────────────────────────
def _cryptoscamdb_docs(limit: int | None) -> list[dict[str, Any]]:
    import pandas as pd

    base = KAGGLE_DIR / "zongaobian_cryptocurrency-scam-dataset"
    docs: list[dict[str, Any]] = []
    for fname in ("urls.csv", "uris.csv"):
        path = base / fname
        if not path.exists():
            logger.warning("cryptoscamdb_missing path=%s", path)
            continue
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            url = str(row.get("url", "")).strip()
            name = str(row.get("name", "")).strip()
            if not url or url.lower() == "nan":
                continue
            category = str(row.get("category", "")).strip()
            subcategory = str(row.get("subcategory", "")).strip()
            description = str(row.get("description", "")).strip()
            if description.lower() == "nan":
                description = ""
            addresses_raw = row.get("addresses", "")
            addresses: list[str] = []
            if isinstance(addresses_raw, str) and addresses_raw.startswith("{"):
                try:
                    parsed = json.loads(addresses_raw.replace("'", '"'))
                    for chain_addrs in parsed.values():
                        if isinstance(chain_addrs, list):
                            addresses.extend(str(a) for a in chain_addrs)
                except (json.JSONDecodeError, AttributeError):
                    pass
            text = (
                f"CryptoScamDB {category} entry: {name}\n"
                f"URL: {url}\nSubcategory: {subcategory}\n"
                f"Description: {description}\n"
                f"Associated addresses: {', '.join(addresses) if addresses else 'none'}\n"
                f"Source: Kaggle cryptocurrency-scam-dataset (CryptoScamDB)"
            )
            docs.append(
                _doc(
                    doc_id=f"{SOURCE}:cryptoscamdb:{url.lower()}",
                    text=text,
                    metadata={
                        "entity": url,
                        "entity_type": "phishing_url",
                        "name": name,
                        "category": category,
                        "subcategory": subcategory,
                        "description": description[:1000],
                        "addresses": addresses,
                        "source": SOURCE,
                        "source_type": "kaggle_cryptoscamdb",
                    },
                    collection=SCAM_COLLECTION,
                    severity="high",
                )
            )
            if limit and len(docs) >= limit:
                return docs
    return docs


# ── Source 3: exchange fraud / wash trading ──────────────────────────────────
def _exchange_fraud_docs(limit: int | None) -> list[dict[str, Any]]:
    import pandas as pd

    base = KAGGLE_DIR / "sergionefedov_crypto-exchange-fraud-and-wash-trading-detection"
    docs: list[dict[str, Any]] = []

    wallets_path = base / "wallets.csv"
    if wallets_path.exists():
        wdf = pd.read_csv(wallets_path)
        for _, row in wdf.iterrows():
            wallet = str(row.get("wallet", "")).strip()
            wtype = str(row.get("wallet_type", "")).strip()
            if not wallet or wtype not in ("manip", "fraud", "wash"):
                continue
            age = row.get("wallet_age_days", "")
            text = (
                f"Exchange manipulation wallet: {wallet}\n"
                f"Wallet type: {wtype}\nWallet age (days): {age}\n"
                f"Source: Kaggle crypto-exchange-fraud-and-wash-trading-detection"
            )
            docs.append(
                _doc(
                    doc_id=f"{SOURCE}:exchwallet:{wallet.lower()}",
                    text=text,
                    metadata={
                        "entity": wallet,
                        "entity_type": "address",
                        "wallet_type": wtype,
                        "wallet_age_days": age,
                        "attack_vector": "wash_trading",
                        "source": SOURCE,
                        "source_type": "kaggle_exchange_fraud",
                    },
                    collection=KNOWN_SCAMS_COLLECTION,
                    severity="high",
                    chain="multi",
                )
            )
            if limit and len(docs) >= limit:
                return docs

    tx_path = base / "transactions.csv"
    if tx_path.exists():
        tdf = pd.read_csv(tx_path)
        manip = tdf[tdf["is_manipulative"] == 1]
        for _, row in manip.iterrows():
            tx_id = str(row.get("tx_id", "")).strip()
            if not tx_id:
                continue
            mtype = str(row.get("manipulation_type", "")).strip()
            text = (
                f"Manipulative exchange transaction {tx_id}\n"
                f"Type: {mtype}\nFrom: {row.get('wallet_from')} → To: {row.get('wallet_to')}\n"
                f"Token: {row.get('token')} | Exchange: {row.get('exchange')} "
                f"({row.get('venue_type')})\n"
                f"Notional USD: {row.get('notional_usd')}\n"
                f"Timestamp: {row.get('timestamp')}\n"
                f"Source: Kaggle crypto-exchange-fraud-and-wash-trading-detection"
            )
            docs.append(
                _doc(
                    doc_id=f"{SOURCE}:exchtx:{tx_id}",
                    text=text,
                    metadata={
                        "entity": tx_id,
                        "entity_type": "transaction",
                        "manipulation_type": mtype,
                        "wallet_from": str(row.get("wallet_from", "")),
                        "wallet_to": str(row.get("wallet_to", "")),
                        "token": str(row.get("token", "")),
                        "exchange": str(row.get("exchange", "")),
                        "notional_usd": float(row.get("notional_usd", 0) or 0),
                        "timestamp": str(row.get("timestamp", "")),
                        "attack_vector": "wash_trading",
                        "source": SOURCE,
                        "source_type": "kaggle_exchange_fraud",
                    },
                    collection=SCAM_COLLECTION,
                    severity="high",
                )
            )
            if limit and len(docs) >= limit:
                return docs
    return docs


# ── Source 4: ethereum fraud (parquet lake) ──────────────────────────────────
def _ethereum_fraud_docs(limit: int | None) -> list[dict[str, Any]]:
    import pandas as pd

    path = LAKE_DIR / "ethereum_fraud" / "transaction_dataset.parquet"
    if not path.exists():
        logger.warning("ethereum_fraud_missing path=%s", path)
        return []
    df = pd.read_parquet(path)
    docs: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        address = str(row.get("Address", "")).strip()
        if not address or address.lower() == "nan":
            continue
        flag = int(row.get("FLAG", 0) or 0)
        text = (
            f"Ethereum account fraud label: {address}\n"
            f"FLAG (1=fraud): {flag}\n"
            f"Sent tnx: {row.get('Sent tnx')} | Received tnx: {row.get('Received Tnx')}\n"
            f"Total Ether sent: {row.get('total Ether sent')} | "
            f"Total ether received: {row.get('total ether received')}\n"
            f"Total ERC20 tnxs: {row.get('Total ERC20 tnxs')}\n"
            f"Source: Kaggle ethereum-frauddetection-dataset"
        )
        docs.append(
            _doc(
                doc_id=f"{SOURCE}:ethfraud:{address.lower()}",
                text=text,
                metadata={
                    "entity": address,
                    "entity_type": "address",
                    "flag": flag,
                    "is_fraud": bool(flag),
                    "sent_tnx": int(row.get("Sent tnx", 0) or 0),
                    "received_tnx": int(row.get("Received Tnx", 0) or 0),
                    "total_ether_sent": float(row.get("total Ether sent", 0) or 0),
                    "total_ether_received": float(row.get("total ether received", 0) or 0),
                    "source": SOURCE,
                    "source_type": "kaggle_ethereum_fraud",
                },
                collection=KNOWN_SCAMS_COLLECTION if flag else SCAM_COLLECTION,
                severity="critical" if flag else "low",
                chain="ethereum",
            )
        )
        if limit and len(docs) >= limit:
            break
    return docs


async def _upsert(store: QdrantVectorStore, docs: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    failed = 0
    embedder = get_embedder()
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        texts = [d["text"] for d in batch]
        try:
            vectors = await embedder.embed(texts)
        except Exception as e:  # noqa: BLE001
            logger.error("embed_batch_failed offset=%d err=%s", i, str(e)[:200])
            failed += len(batch)
            continue
        for doc, vec in zip(batch, vectors, strict=False):
            if not vec:
                failed += 1
                continue
            ok = await store.insert(
                doc_id=doc["doc_id"],
                collection=doc["collection"],
                embedding=vec,
                content=doc["text"],
                metadata=doc["metadata"],
                source=SOURCE,
                severity=doc["severity"],
                chain=doc["chain"],
            )
            if ok:
                inserted += 1
            else:
                failed += 1
        logger.info(
            "progress %d/%d inserted=%d failed=%d",
            min(i + BATCH_SIZE, len(docs)),
            len(docs),
            inserted,
            failed,
        )
    return {"inserted": inserted, "failed": failed}


async def run(dry_run: bool, limit: int | None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "source": SOURCE,
        "scam_collection": SCAM_COLLECTION,
        "known_scams_collection": KNOWN_SCAMS_COLLECTION,
        "dry_run": dry_run,
    }

    builders = {
        "nft_rugpulls": _nft_rugpull_docs,
        "cryptoscamdb": _cryptoscamdb_docs,
        "exchange_fraud": _exchange_fraud_docs,
        "ethereum_fraud": _ethereum_fraud_docs,
    }
    all_docs: list[dict[str, Any]] = []
    for name, builder in builders.items():
        docs = builder(limit)
        stats[f"{name}_docs"] = len(docs)
        logger.info("built %s docs=%d", name, len(docs))
        all_docs.extend(docs)

    stats["total_docs"] = len(all_docs)
    if dry_run:
        logger.info("dry_run total_docs=%d", len(all_docs))
        return stats

    if not all_docs:
        logger.warning("no docs to ingest")
        return stats

    store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
    res = await _upsert(store, all_docs)
    stats["inserted"] = res["inserted"]
    stats["failed"] = res["failed"]
    await store.close()
    logger.info("done inserted=%d failed=%d", res["inserted"], res["failed"])
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Kaggle scam/fraud → RugBrain")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="max docs per source")
    args = parser.parse_args()
    stats = asyncio.run(run(args.dry_run, args.limit))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
