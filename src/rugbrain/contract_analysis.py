"""Contract security analysis — source retrieval, static heuristics, knowledge compare.

Part 4 of the RugBrain contract-knowledge expansion. When the
``/api/v1/analyze/{address}`` endpoint is called, this module:

  1. Retrieves the verified contract source (Sourcify first — free, no API
     key — then Etherscan when ETHERSCAN_API_KEY is configured).
  2. Runs deterministic static heuristics over the source (reentrancy
     ordering, tx.origin, selfdestruct, delegatecall, unrestricted
     privileged functions, blacklists, unlimited approvals, ...).
  3. Compares the source against the curated knowledge collections
     (``solidity_patterns`` / ``contract_security`` / ``audit_findings``)
     to find similar known-good / known-bad contracts and matching
     vulnerability patterns.
  4. Returns a security assessment alongside the existing pattern-matching
     verdict.

Every step fails open: a missing source, embedder, or Qdrant just produces
a smaller (benign) response — the analyze endpoint never breaks because of
contract analysis.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logger = logging.getLogger(__name__)

# ── Sourcify chain → chain-id mapping (subset used by RugBrain) ───────
CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
    "base": 8453,
    "avalanche": 43114,
    "gnosis": 100,
    "fantom": 250,
    "linea": 59144,
    "scroll": 534352,
    "zksync": 324,
    "sepolia": 11155111,
}

SOURCIFY_BASE = "https://repo.sourcify.dev/contracts"
ETHERSCAN_API = "https://api.etherscan.io/api"
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")

# Knowledge collections searched during source comparison.
SOLIDITY_PATTERNS = "solidity_patterns"
SECURITY_COLLECTION = "contract_security"
AUDIT_COLLECTION = "audit_findings"
KNOWLEDGE_COLLECTIONS = (SOLIDITY_PATTERNS, SECURITY_COLLECTION, AUDIT_COLLECTION)

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# ── Static heuristic rules ─────────────────────────────────────────────

# Regex rules: (rule_id, severity, title, pattern, description, suggestion)
_REGEX_RULES: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "tx-origin-auth",
        "critical",
        "tx.origin used for authorization",
        r"\btx\.origin\b",
        "tx.origin is the original external caller, not msg.sender. A phishing "
        "contract can call this contract on the user's behalf and pass the check.",
        "Authorize with msg.sender (e.g. OpenZeppelin Ownable/AccessControl).",
    ),
    (
        "selfdestruct",
        "critical",
        "selfdestruct present",
        r"\bselfdestruct\b|\bSUICIDE\b",
        "selfdestruct sends the entire balance and removes code. If reachable by "
        "an attacker, funds and functionality are destroyed.",
        "Remove selfdestruct; gate behind multisig + timelock if truly needed.",
    ),
    (
        "delegatecall",
        "high",
        "delegatecall to variable target",
        r"\.delegatecall\s*\(",
        "delegatecall executes callee code in this contract's storage context. "
        "A user-influenced target can overwrite owner/storage and take over the contract.",
        "Only delegatecall to whitelisted implementations; gate with onlyOwner.",
    ),
    (
        "unlimited-approval",
        "info",
        "unlimited approval pattern",
        r"approve\s*\([^,]+,\s*type\s*\(\s*uint256\s*\)\s*\.\s*max",
        "Approving type(uint256).max is a wallet-drainer enabler; victims should "
        "only approve exact amounts.",
        "Use exact-amount approvals and revoke stale approvals.",
    ),
    (
        "blacklist",
        "medium",
        "blacklist logic present",
        r"\bblacklist(?:ed)?\b",
        "Owner-controlled blacklists can freeze arbitrary holders while the deployer dumps.",
        "Require timelock + transparency for blacklists; avoid in tokens.",
    ),
    (
        "unchecked",
        "info",
        "unchecked arithmetic block",
        r"\bunchecked\s*\{",
        "unchecked arithmetic can wrap silently; only safe when bounds are proven.",
        "Prefer checked arithmetic (Solidity >= 0.8) or add require guards.",
    ),
    (
        "ecrecover",
        "info",
        "raw ecrecover used",
        r"\becrecover\s*\(",
        "Raw ecrecover is malleable and returns 0x0 on failure; prefer OpenZeppelin ECDSA.recover.",
        "Use ECDSA.recover with low-s enforcement.",
    ),
    (
        "block-timestamp",
        "low",
        "block.timestamp dependence",
        r"\bblock\.timestamp\b",
        "Miners influence block.timestamp within limits; using it for "
        "randomness or critical thresholds is manipulable.",
        "Use Chainlink VRF for randomness; timestamps only for coarse deadlines.",
    ),
    (
        "weak-randomness",
        "low",
        "chain-attribute randomness",
        r"\bblock\.(?:difficulty|prevrandao|hash|number|basefee)\b",
        "Chain attributes are predictable/manipulable; weak randomness enables "
        "front-running of draws.",
        "Use a verifiable random function (VRF).",
    ),
    (
        "assembly",
        "info",
        "inline assembly present",
        r"\bassembly\b",
        "Inline assembly bypasses compiler safety; high-risk when handling storage or calldata.",
        "Minimize assembly; add comments + tests for every asm block.",
    ),
    (
        "abi-encode-packed",
        "low",
        "abi.encodePacked with dynamic args",
        r"abi\.encodePacked\s*\([^)]*bytes",
        "abi.encodePacked with multiple dynamic types can collide; use it only "
        "with fixed-size types.",
        "Use abi.encode for hash inputs with multiple dynamic args.",
    ),
    (
        "create2",
        "info",
        "CREATE2 used",
        r"\bcreate2\b",
        "CREATE2 lets deployers pre-compute addresses and 'update' bytecode at "
        "the same address (a rug vector when used for token factories).",
        "Verify factory access control and immutability claims.",
    ),
)

# State-variable keywords that indicate a value-moving write.
_STATE_KEYWORDS = (
    "balance",
    "balances",
    "totalSupply",
    "shares",
    "amounts",
    "allowance",
    "supply",
    "rewards",
    "debt",
    "collateral",
    "locked",
    "count",
)

# External interaction lines (raw value call or high-level transfer).
_EXTERNAL_CALL_RE = re.compile(r"\.(?:call|transfer|send)\s*(\{value:|\(|$)")
_STATE_WRITE_RE = re.compile(
    r"\b(?:balances?|totalSupply|shares|amounts?|allowance|supply|rewards?|debt|collateral|locked)\b.*(?:[-+]=|--|\+\+|=\s*[^=])|delete\s+\w+\["
)
_FUNC_DEF_RE = re.compile(r"\bfunction\s+(\w+)\s*\(")
_AUTH_RE = re.compile(
    r"onlyOwner|onlyRole|onlyAdmin|require\s*\(\s*msg\.sender|_checkOwner|hasRole"
)
_PRIVILEGED_NAMES = (
    "mint",
    "withdraw",
    "setOwner",
    "setAdmin",
    "transferOwnership",
    "setTax",
    "setFee",
    "pause",
    "unpause",
    "upgrade",
    "setImplementation",
    "rescue",
    "sweep",
)


def _scan_reentrancy(lines: list[str], lookahead: int = 6) -> list[dict[str, Any]]:
    """Line-aware CEI check: external value call followed by a state write."""
    findings: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if not _EXTERNAL_CALL_RE.search(line):
            continue
        window = lines[idx + 1 : idx + 1 + lookahead]
        if any(_STATE_WRITE_RE.search(w) for w in window):
            findings.append(
                {
                    "rule": "reentrancy-unsafe-order",
                    "severity": "high",
                    "title": "External call before state update (possible reentrancy)",
                    "evidence": line.strip()[:200],
                    "description": (
                        "An external call appears before a state write in the "
                        "same function. A malicious callee can re-enter and "
                        "observe stale state (DAO hack, Cream, Euler)."
                    ),
                    "suggestion": "Apply CEI (checks-effects-interactions): update state before the external call and add a reentrancy guard.",
                }
            )
        else:
            findings.append(
                {
                    "rule": "raw-external-call",
                    "severity": "info",
                    "title": "Raw external call present",
                    "evidence": line.strip()[:200],
                    "description": "Low-level call/transfer/send used; verify CEI ordering and the returned bool.",
                    "suggestion": "Prefer safe transfer with require(ok) and nonReentrant.",
                }
            )
    return findings


def _scan_privileged_functions(lines: list[str], body_window: int = 8) -> list[dict[str, Any]]:
    """Flag privileged-looking functions that lack an auth check in their body."""
    findings: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        m = _FUNC_DEF_RE.search(line)
        if not m:
            continue
        name = m.group(1).lower()
        if name not in _PRIVILEGED_NAMES:
            continue
        window = "\n".join(lines[idx : idx + body_window])
        if not _AUTH_RE.search(window):
            findings.append(
                {
                    "rule": f"unrestricted-{name}",
                    "severity": "medium" if name in ("pause", "unpause") else "high",
                    "title": f"Privileged function '{name}' without visible access control",
                    "evidence": line.strip()[:200],
                    "description": (
                        f"A function named '{name}' was found without an "
                        "owner/role check in its body. If callable by anyone it "
                        "can mint, withdraw, or change parameters."
                    ),
                    "suggestion": "Add onlyOwner/onlyRole guards and verify with the deployer's access-control setup.",
                }
            )
    return findings


def static_security_scan(source: str) -> list[dict[str, Any]]:
    """Run deterministic static heuristics over *source*.

    Returns a list of findings::

        [{"rule", "severity", "title", "evidence", "description", "suggestion"}]

    Ordered by severity (critical → info). Never raises.
    """
    if not source:
        return []
    findings: list[dict[str, Any]] = []
    for rule_id, severity, title, pattern, description, suggestion in _REGEX_RULES:
        m = re.search(pattern, source, re.IGNORECASE)
        if m:
            start = max(0, m.start() - 60)
            findings.append(
                {
                    "rule": rule_id,
                    "severity": severity,
                    "title": title,
                    "evidence": source[start : m.end() + 60].replace("\n", " ").strip()[:200],
                    "description": description,
                    "suggestion": suggestion,
                }
            )

    lines = source.splitlines()
    findings.extend(_scan_reentrancy(lines))
    findings.extend(_scan_privileged_functions(lines))

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: order.get(f["severity"], 5))
    return findings


# ── Source retrieval ───────────────────────────────────────────────────


def _sourcify_sources_to_text(metadata: dict[str, Any]) -> tuple[str, str, str]:
    """Combine Sourcify ``metadata.json`` sources into one text block.

    Returns ``(combined_source, contract_name, compiler_version)``.
    """
    sources: dict[str, Any] = metadata.get("sources", {}) or {}
    parts: list[str] = []
    name = ""
    for path, info in sources.items():
        content = ""
        if isinstance(info, dict):
            content = info.get("content", "")
            if not content and info.get("urls"):
                content = f"// see {info['urls']}"
        elif isinstance(info, str):
            content = info
        if not name and content:
            name = str(path).split("/")[-1].removesuffix(".sol")
        if content:
            parts.append(f"// ===== {path} =====")
            parts.append(str(content))
    compiler = metadata.get("compiler", {}) or {}
    version = str(compiler.get("version", ""))
    return "\n".join(parts), name, version


def _etherscan_sources_to_text(row: dict[str, Any]) -> tuple[str, str, str]:
    """Combine an Etherscan getsourcecode row into one text block."""
    name = str(row.get("ContractName", ""))
    version = str(row.get("CompilerVersion", ""))
    source_code = str(row.get("SourceCode", "") or "")
    if source_code.startswith("{"):
        # Multi-file source is JSON-encoded inside SourceCode.
        try:
            parsed = (
                source_code[1:-1]
                if source_code.startswith("{{") and source_code.endswith("}}")
                else source_code
            )
            data = __import__("json").loads(parsed)
            sources = data.get("sources", {}) if isinstance(data, dict) else {}
            parts: list[str] = []
            for path, info in sources.items():
                content = info.get("content", "") if isinstance(info, dict) else str(info)
                if content:
                    parts.append(f"// ===== {path} =====")
                    parts.append(content)
            if parts:
                return "\n".join(parts), name, version
        except (ValueError, TypeError):
            pass
    return source_code, name, version


async def fetch_contract_source(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Retrieve verified source for *address* — Sourcify, then Etherscan.

    Returns::

        {"address", "chain", "backend": "sourcify"|"etherscan"|"none",
         "contract_name", "compiler_version", "source", "source_len"}

    ``backend == "none"`` when no verified source is available.
    """
    address = address.strip().lower()
    chain_key = (chain or "ethereum").strip().lower()
    chain_id = CHAIN_IDS.get(chain_key)
    result: dict[str, Any] = {
        "address": address,
        "chain": chain_key,
        "backend": "none",
        "contract_name": "",
        "compiler_version": "",
        "source": "",
        "source_len": 0,
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        if chain_id:
            for match in ("full_match", "partial_match"):
                try:
                    r = await client.get(
                        f"{SOURCIFY_BASE}/{match}/{chain_id}/{address}/metadata.json"
                    )
                    if r.status_code != 200:
                        continue
                    metadata = r.json()
                    combined, name, version = _sourcify_sources_to_text(metadata)
                    if combined.strip():
                        result.update(
                            backend="sourcify",
                            contract_name=name,
                            compiler_version=version,
                            source=combined,
                            source_len=len(combined),
                        )
                        return result
                except Exception as e:
                    logger.debug("sourcify_fetch_failed match=%s err=%s", match, str(e)[:120])

        if ETHERSCAN_API_KEY and chain_key in (
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "base",
        ):
            chain_api = {
                "bsc": "api.bscscan.com",
                "polygon": "api.polygonscan.com",
                "arbitrum": "api.arbiscan.io",
                "optimism": "api-optimistic.etherscan.io",
                "base": "api.basescan.org",
            }.get(chain_key, "api.etherscan.io")
            try:
                r = await client.get(
                    f"https://{chain_api}/api",
                    params={
                        "module": "contract",
                        "action": "getsourcecode",
                        "address": address,
                        "apikey": ETHERSCAN_API_KEY,
                    },
                )
                if r.status_code == 200:
                    rows = r.json().get("result", []) or []
                    if rows and rows[0].get("SourceCode"):
                        combined, name, version = _etherscan_sources_to_text(rows[0])
                        if combined.strip():
                            result.update(
                                backend="etherscan",
                                contract_name=name,
                                compiler_version=version,
                                source=combined,
                                source_len=len(combined),
                            )
            except Exception as e:
                logger.debug("etherscan_fetch_failed err=%s", str(e)[:120])
    return result


# ── Knowledge comparison ───────────────────────────────────────────────


async def _knowledge_compare(
    source: str,
    chain: str,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Embed *source* and compare against the knowledge collections.

    Returns ``(similar_good, similar_bad, vuln_matches)`` where good/bad come
    from ``solidity_patterns`` (metadata.verdict) and vuln_matches come from
    ``contract_security`` / ``audit_findings``.
    """
    embedder = Qwen3Embedder()
    store = QdrantVectorStore()
    try:
        try:
            embedding = await embedder.embed_one(source[:8000])
        except Exception as e:
            logger.warning("contract_embed_failed: %s", str(e)[:120])
            return [], [], []
        if not embedding:
            return [], [], []

        similar_good: list[dict[str, Any]] = []
        similar_bad: list[dict[str, Any]] = []
        try:
            hits = await store.search(
                embedding,
                collection=SOLIDITY_PATTERNS,
                limit=limit * 4,
                min_similarity=0.45,
            )
            for h in hits:
                verdict = (h.get("metadata", {}) or {}).get("verdict", "")
                item = {
                    "contract_id": (h.get("metadata", {}) or {}).get("contract_id", ""),
                    "title": (h.get("metadata", {}) or {}).get("title", ""),
                    "similarity": h.get("similarity", 0.0),
                    "content": (h.get("content", "") or "")[:400],
                    "source": h.get("source", ""),
                }
                if verdict == "good":
                    similar_good.append(item)
                elif verdict == "bad":
                    similar_bad.append(item)
        except Exception as e:
            logger.debug("solidity_patterns_search_failed: %s", str(e)[:120])

        vuln_matches: list[dict[str, Any]] = []
        try:
            for collection in (SECURITY_COLLECTION, AUDIT_COLLECTION):
                hits = await store.search(
                    embedding,
                    collection=collection,
                    limit=limit,
                    min_similarity=0.4,
                )
                for h in hits:
                    meta = h.get("metadata", {}) or {}
                    vuln_matches.append(
                        {
                            "collection": collection,
                            "pattern_id": meta.get("pattern_id")
                            or meta.get("finding_id")
                            or meta.get("contract_id")
                            or "",
                            "title": meta.get("title", ""),
                            "category": meta.get("category", ""),
                            "severity": h.get("severity", ""),
                            "similarity": h.get("similarity", 0.0),
                            "content": (h.get("content", "") or "")[:400],
                        }
                    )
        except Exception as e:
            logger.debug("security_collection_search_failed: %s", str(e)[:120])

        similar_good = similar_good[:limit]
        similar_bad = similar_bad[:limit]
        vuln_matches.sort(key=lambda m: m.get("similarity", 0.0), reverse=True)
        return similar_good, similar_bad, vuln_matches[: limit * 2]
    finally:
        await store.close()


def _aggregate_assessment(
    static: list[dict[str, Any]],
    similar_good: list[dict[str, Any]],
    similar_bad: list[dict[str, Any]],
    vuln_matches: list[dict[str, Any]],
) -> tuple[str, str]:
    """Combine signals into ``(assessment, summary)``."""
    worst = "info"
    if any(f["severity"] == "critical" for f in static):
        worst = "critical"
    elif any(f["severity"] == "high" for f in static):
        worst = "high"
    elif any(f["severity"] == "medium" for f in static):
        worst = "medium"
    elif any(f["severity"] == "low" for f in static):
        worst = "low"

    bad_similarity = max([s.get("similarity", 0.0) for s in similar_bad], default=0.0)
    good_similarity = max([s.get("similarity", 0.0) for s in similar_good], default=0.0)
    worst_vuln = max([m.get("similarity", 0.0) for m in vuln_matches], default=0.0)

    if worst in ("critical", "high") or bad_similarity >= 0.6 or worst_vuln >= 0.7:
        assessment = "high"
    elif worst == "medium" or bad_similarity >= 0.5:
        assessment = "medium"
    elif worst == "low" or bad_similarity >= 0.45 or good_similarity >= 0.6:
        assessment = "low"
    else:
        assessment = "info"

    parts: list[str] = []
    if bad_similarity > 0:
        parts.append(f"resembles known-bad contracts ({bad_similarity:.0%} max similarity)")
    if good_similarity > 0:
        parts.append(f"resembles known-good contracts ({good_similarity:.0%} max similarity)")
    if worst != "info":
        parts.append(f"static scan found {worst} severity signals")
    summary = "; ".join(parts) if parts else "no strong security signals detected"
    return assessment, summary


async def analyze_contract_security(
    address: str,
    chain: str = "ethereum",
    limit: int = 5,
) -> dict[str, Any]:
    """Full contract-security pipeline for *address* (fail-open).

    Returns a dict describing source retrieval, static findings, knowledge
    matches, and an aggregate assessment. Never raises.
    """
    base: dict[str, Any] = {
        "address": address,
        "chain": chain,
        "source_fetched": False,
        "backend": "none",
        "contract_name": "",
        "compiler_version": "",
        "assessment": "unknown",
        "summary": "",
        "static_findings": [],
        "knowledge_matches": [],
        "similar_contracts": {"good": [], "bad": []},
        "error": "",
    }
    if not _ADDRESS_RE.fullmatch((address or "").strip()):
        base["error"] = "invalid_address"
        return base

    try:
        fetched = await fetch_contract_source(address, chain)
    except Exception as e:
        base["error"] = f"source_fetch_failed: {str(e)[:120]}"
        logger.warning("contract_source_fetch_failed addr=%s err=%s", address, str(e)[:120])
        return base

    if fetched.get("backend") == "none" or not fetched.get("source"):
        base["error"] = "no_verified_source"
        return base

    base.update(
        source_fetched=True,
        backend=fetched["backend"],
        contract_name=fetched.get("contract_name", ""),
        compiler_version=fetched.get("compiler_version", ""),
    )
    source = str(fetched["source"])
    base["source_len"] = fetched.get("source_len", len(source))

    static = static_security_scan(source)
    similar_good, similar_bad, vuln_matches = await _knowledge_compare(source, chain, limit)
    assessment, summary = _aggregate_assessment(static, similar_good, similar_bad, vuln_matches)

    base["static_findings"] = static
    base["knowledge_matches"] = vuln_matches
    base["similar_contracts"] = {"good": similar_good, "bad": similar_bad}
    base["assessment"] = assessment
    base["summary"] = summary
    return base


__all__ = [
    "CHAIN_IDS",
    "KNOWLEDGE_COLLECTIONS",
    "analyze_contract_security",
    "fetch_contract_source",
    "static_security_scan",
]
