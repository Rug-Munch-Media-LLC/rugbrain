"""Curated crypto security-pattern knowledge for ``ingest_security_patterns.py``.

Moved verbatim from ``scripts/contract_knowledge_data.py``; split so every module stays under 200
lines. Pure data: no I/O, no third-party imports.
"""

from __future__ import annotations

from typing import Any

from rugbrain.knowledge.contract_patterns import part_1 as _part_1
from rugbrain.knowledge.contract_patterns import part_2 as _part_2
from rugbrain.knowledge.contract_patterns import part_3 as _part_3
from rugbrain.knowledge.contract_patterns import part_4 as _part_4
from rugbrain.knowledge.contract_patterns import part_5 as _part_5
from rugbrain.knowledge.contract_patterns import part_6 as _part_6
from rugbrain.knowledge.contract_patterns import part_7 as _part_7

SECURITY_PATTERNS: list[dict[str, Any]] = [*_part_1.ITEMS, *_part_2.ITEMS, *_part_3.ITEMS, *_part_4.ITEMS, *_part_5.ITEMS, *_part_6.ITEMS, *_part_7.ITEMS]

SWC_FALLBACK_TITLES: dict[str, str] = {
    "SWC-100": "Function Default Visibility",
    "SWC-101": "Integer Overflow and Underflow",
    "SWC-102": "Outdated Compiler Version",
    "SWC-103": "Floating Pragma",
    "SWC-104": "Unchecked Call Return Value",
    "SWC-105": "Unprotected Ether Withdrawal",
    "SWC-106": "Unprotected SELFDESTRUCT Instruction",
    "SWC-107": "Reentrancy",
    "SWC-108": "State Variable Default Visibility",
    "SWC-109": "Uninitialized Storage Pointer",
    "SWC-110": "Assert Violation",
    "SWC-111": "Use of Deprecated Solidity Functions",
    "SWC-112": "DELEGATECALL to Untrusted Callee",
    "SWC-113": "DoS with Failed Call",
    "SWC-114": "Transaction Order Dependence",
    "SWC-115": "Authorization through tx.origin",
    "SWC-116": "Block Values as a Proxy for Time",
    "SWC-117": "Signature Malleability",
    "SWC-118": "Incorrect Constructor Name",
    "SWC-119": "Shadowing State Variables",
    "SWC-120": "Weak Sources of Randomness from Chain Attributes",
    "SWC-121": "Missing Protection against Signature Replay Attacks",
    "SWC-122": "Missing Input Validation",
    "SWC-123": "Requirement Violation",
    "SWC-124": "Write to Arbitrary Storage Location",
    "SWC-125": "Incorrect Inheritance Order",
    "SWC-126": "Insufficient Gas Griefing",
    "SWC-127": "Arbitrary Jump with Function Type Variable",
    "SWC-128": "DoS With Block Gas Limit",
    "SWC-129": "Typographical Error",
    "SWC-130": "Right-To-Left-Override character U+202E",
    "SWC-131": "Presence of unused variables",
    "SWC-132": "Unexpected Ether balance",
    "SWC-133": "Hash Collisions With Multiple Variable Length Arguments",
    "SWC-134": "Message call with hardcoded gas amount",
    "SWC-135": "Code With No Effects",
    "SWC-136": "Unencrypted Private Data On-Chain",
}

from rugbrain.knowledge.contract_patterns import part_1 as _part_1  # noqa: E402
from rugbrain.knowledge.contract_patterns import part_2 as _part_2  # noqa: E402

AUDIT_FINDINGS: list[dict[str, Any]] = [*_part_1.ITEMS, *_part_2.ITEMS]
