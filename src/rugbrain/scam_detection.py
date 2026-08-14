import json
import logging
from typing import Any

from rugbrain import search_service as _ss
from rugbrain.crypto_embeddings import (
    KNOWN_SCAM_PATTERNS,
    get_embedder,
)
from rugbrain.feature_extractors import extract_contract_features

logger = logging.getLogger(__name__)


async def detect_scam_patterns(
    token_data: dict[str, Any],
    threshold: float = 0.65,
) -> dict[str, Any]:
    """
    Compare a token against all known scam patterns.
    Returns matched patterns with similarity scores.
    """
    embedder = await get_embedder()

    # Pre-embed patterns on first call if not already done
    if not _ss._pattern_cache_loaded:
        await _ss._preembed_scam_patterns()

    # Build a token embedding
    code = token_data.get("contract_code", "")
    desc = token_data.get("description", "")
    name = token_data.get("name", "Unknown")
    symbol = token_data.get("symbol", "???")

    # Quick pre-filter: check code for exact keyword matches (fast, no API)
    code_lower = code.lower() if code else ""
    quick_matches: list[dict[str, Any]] = []
    for pattern in KNOWN_SCAM_PATTERNS:
        if not pattern["code_snippets"]:
            continue
        hits = sum(1 for s in pattern["code_snippets"] if s.lower() in code_lower)
        if hits > 0:
            quick_matches.append(
                {
                    "pattern": pattern["name"],
                    "severity": pattern["severity"],
                    "quick_match_score": hits / len(pattern["code_snippets"]),
                    "matched_snippets": hits,
                }
            )

    # Deep semantic comparison
    token_result = await embedder.embed_token_scam(
        name=name,
        symbol=symbol,
        description=desc,
        contract_code=code,
        chain=token_data.get("chain", "solana"),
        metadata=token_data,
    )

    # Vector layout:
    #   token  = [semantic(sem_dim) | code(128) | behavioral(64) | wallet(64)]
    #   pattern = [semantic(sem_dim) | code(128)]
    # Both have a common [semantic | code] prefix if they use the same model.
    # For the semantic comparison, use only the common prefix.
    # For the code comparison, compare the 128-dim code sections.

    token_total = len(token_result.vector)
    # If token vector has multi-head layout, extract heads by known offsets
    if token_total > 128 + 64 + 64:
        # Full multi-head: [semantic | code(128) | behavioral(64) | wallet(64)]
        token_sem_end = token_total - 128 - 64 - 64
        token_sem = token_result.vector[:token_sem_end]
        token_code = token_result.vector[token_sem_end : token_sem_end + 128]
    elif token_total > 128:
        # Partial: [semantic | code(128)] - no behavioral/wallet
        token_sem_end = token_total - 128
        token_sem = token_result.vector[:token_sem_end]
        token_code = token_result.vector[token_sem_end : token_sem_end + 128]
    else:
        # Only semantic - use as-is
        token_sem = token_result.vector
        token_code = [0.0] * 128

    deep_matches: list[dict[str, Any]] = []
    for pattern in KNOWN_SCAM_PATTERNS:
        # Use pre-embedded cache if available, otherwise embed on-demand and cache
        pattern_name = pattern["name"]
        if pattern_name in _ss._pattern_cache:
            pattern_result = _ss._pattern_cache[pattern_name]
        else:
            pattern_result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"],
                description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )
            # Cache for future use
            _ss._pattern_cache[pattern_name] = pattern_result
            # Also persist to Redis
            try:
                r = await _ss._get_redis()
                redis_key = f"rag:pattern_cache:{pattern_name}"
                await r.setex(
                    redis_key,
                    86400 * 30,
                    json.dumps(
                        {
                            "vector": pattern_result.vector,
                            "dims": pattern_result.dims,
                            "model": pattern_result.model,
                            "head": pattern_result.head,
                        }
                    ),
                )
            except Exception:
                logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

        # Extract pattern semantic + code portions
        pat_total = len(pattern_result.vector)
        if pat_total > 128:
            pat_sem_end = pat_total - 128
            pattern_sem = pattern_result.vector[:pat_sem_end]
            pattern_result.vector[pat_sem_end : pat_sem_end + 128]
        else:
            pattern_sem = pattern_result.vector

        # Compare same-length semantic portions (may differ if different models)
        min_sem = min(len(token_sem), len(pattern_sem))
        if min_sem == 0:
            continue
        sem_sim = embedder.cosine_similarity(token_sem[:min_sem], pattern_sem[:min_sem])

        # Code similarity
        pat_code_features = (
            extract_contract_features("\n".join(pattern.get("code_snippets", []))).tolist()
            if pattern.get("code_snippets")
            else [0.0] * 128
        )
        code_sim = (
            embedder.cosine_similarity(token_code, pat_code_features)
            if any(pat_code_features)
            else 0.0
        )

        combined = 0.7 * sem_sim + 0.3 * code_sim

        if combined >= threshold:
            deep_matches.append(
                {
                    "pattern": pattern["name"],
                    "severity": pattern["severity"],
                    "description": pattern["description"],
                    "similarity": round(combined, 4),
                    "semantic_sim": round(sem_sim, 4),
                    "code_sim": round(code_sim, 4),
                }
            )

    deep_matches.sort(key=lambda x: x["similarity"], reverse=True)

    return {
        "quick_matches": quick_matches,
        "deep_matches": deep_matches,
        "highest_threat": deep_matches[0]["pattern"] if deep_matches else "none",
        "threat_severity": deep_matches[0]["severity"] if deep_matches else "low",
    }
