import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from rugbrain import search_service as _ss

logger = logging.getLogger(__name__)


async def seed_known_scams() -> dict[str, Any]:
    """Seed the RAG database with known scam patterns."""
    from rugbrain.crypto_embeddings import KNOWN_SCAM_PATTERNS, get_embedder

    r = await _ss._get_redis()
    existing = await r.scard("rag:idx:known_scams")
    if existing > 0 and _ss._seeded:
        return {"status": "already_seeded", "count": existing}

    embedder = await get_embedder()
    count = 0

    for pattern in KNOWN_SCAM_PATTERNS:
        try:
            pid = hashlib.sha256(f"scam:{pattern['name']}".encode()).hexdigest()[:16]
            result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"],
                description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )

            doc = {
                "id": pid,
                "collection": "known_scams",
                "vector": result.vector,
                "dims": result.dims,
                "model": result.model,
                "metadata": pattern,
                "content": pattern["description"],
                "stored_at": datetime.now(UTC).isoformat(),
            }
            key = f"rag:known_scams:{pid}"
            await r.set(key, json.dumps(doc))  # permanent - seed data should never expire
            await r.sadd("rag:idx:known_scams", pid)
            count += 1
            logger.info("Seeded scam pattern: %s", pattern["name"])

        except Exception as e:
            logger.error("Failed to seed %s: %s", pattern["name"], e)

    _ss._seeded = True
    return {
        "status": "seeded",
        "count": count,
        "patterns": [p["name"] for p in KNOWN_SCAM_PATTERNS],
    }
