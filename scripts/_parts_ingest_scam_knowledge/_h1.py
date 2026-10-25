"""Header chunk 1/2 of scripts/ingest_scam_knowledge.py (imports + constants)."""

from __future__ import annotations

import argparse

import asyncio

import hashlib

import json

import logging

import os

import re

import shutil

import subprocess

import tempfile

import uuid

from datetime import UTC, datetime

from pathlib import Path

from typing import Any

import httpx

import numpy as np

from rugbrain.ingest_smart import (
    dedup_vectors,
    filter_by_severity,
    git_refresh,
    repo_last_updated,
    smart_split,
    sort_by_priority,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

logger = logging.getLogger("ingest_scam_knowledge")

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")

QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1").rstrip("/")

QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")

EMBEDDING_DIM = int(
    os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM")
    or os.getenv("RAG_EMBED_DIMS") or "4096"
)

KNOWLEDGE_DIR = Path(os.getenv("RAG_KNOWLEDGE_DIR", "/tmp/rag-knowledge"))  # noqa: S108  # nosec B108

CHECKPOINT_PATH = Path(
    os.getenv("RAG_KNOWLEDGE_CHECKPOINT", str(KNOWLEDGE_DIR / "checkpoint.json"))
)

BATCH_SIZE = int(os.getenv("RAG_INGEST_BATCH_SIZE", "50"))

CHUNK_SIZE = 700

CHUNK_OVERLAP = 70

from rugbrain.qdrant_store.lane_resolver import resolve_collection as _resolve

COLLECTION_DEFI_EXPLOITS = _resolve("defi_exploits") or "defi_exploits_8b"

COLLECTION_SCAM_PATTERNS = _resolve("scam_patterns") or "scam_patterns_8b"

COLLECTION_VULNERABILITIES = _resolve("crypto_vulnerabilities") or "crypto_vulnerabilities_8b"

COLLECTION_CONTRACT_AUDITS = _resolve("contract_audits") or "contract_audits_8b"

SOURCE_COLLECTIONS: dict[str, str] = {
    "defihacklabs": COLLECTION_DEFI_EXPLOITS,
    "rekt_news": COLLECTION_DEFI_EXPLOITS,
    "defillama_hacks": COLLECTION_DEFI_EXPLOITS,
    "web3hacks": COLLECTION_DEFI_EXPLOITS,
    "postmortems": COLLECTION_DEFI_EXPLOITS,
    "cryptoscamdb": COLLECTION_SCAM_PATTERNS,
    "slowmist": COLLECTION_VULNERABILITIES,
    "swc_registry": COLLECTION_VULNERABILITIES,
    "immunefi": COLLECTION_VULNERABILITIES,
    "openzeppelin_audits": COLLECTION_CONTRACT_AUDITS,
}

DEDUP_THRESHOLD_DEFAULT = 0.95

ATTACK_VECTORS = (
    "reentrancy",
    "flash-loan",
    "oracle-manipulation",
    "access-control",
    "bridge-exploit",
    "governance-attack",
    "signature-malleability",
    "integer-overflow",
    "key-compromise",
    "rug-pull",
    "honeypot",
    "phishing",
    "withdrawal-exploit",
    "denial-of-service",
    "front-running",
    "supply-chain",
    "input-validation",
    "smart-contract-exploit",
    "unknown",
)

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "unknown": 0}

CHAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "ethereum": ("ethereum", "eth ", "erc-20", "erc-721", "evm", "mainnet"),
    "bsc": ("bsc", "binance smart chain", "bnb chain", "bep-20"),
    "solana": ("solana", "spl token", "spl-token"),
    "polygon": ("polygon", "matic"),
    "arbitrum": ("arbitrum", "arb "),
    "optimism": ("optimism", "op mainnet"),
    "base": (" base ", "base network"),
    "avalanche": ("avalanche", "avax"),
    "tron": ("tron", "trc-20"),
    "fantom": ("fantom", " ftm "),
    "multi": ("multi-chain", "cross-chain", "multiple chains"),
}


__all__ = [
    "annotations",
    "argparse",
    "asyncio",
    "hashlib",
    "json",
    "logging",
    "os",
    "re",
    "shutil",
    "subprocess",
    "tempfile",
    "uuid",
    "UTC",
    "datetime",
    "Path",
    "Any",
    "httpx",
    "np",
    "dedup_vectors",
    "filter_by_severity",
    "git_refresh",
    "repo_last_updated",
    "smart_split",
    "sort_by_priority",
    "logger",
    "QDRANT_URL",
    "QWEN_EMBED_URL",
    "QWEN_EMBED_MODEL_NAME",
    "EMBEDDING_DIM",
    "KNOWLEDGE_DIR",
    "CHECKPOINT_PATH",
    "BATCH_SIZE",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "_resolve",
    "COLLECTION_DEFI_EXPLOITS",
    "COLLECTION_SCAM_PATTERNS",
    "COLLECTION_VULNERABILITIES",
    "COLLECTION_CONTRACT_AUDITS",
    "SOURCE_COLLECTIONS",
    "DEDUP_THRESHOLD_DEFAULT",
    "ATTACK_VECTORS",
    "SEVERITY_RANK",
    "CHAIN_ALIASES",
]
