"""Sync MCP security patterns into the RAG ``coding_knowledge`` collection.

Pulls patterns from the MCP (``list_patterns``), converts each into a
coding-knowledge doc, and ingests them via the RAG's own store so the RAG
can answer security questions even when the MCP is offline.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from rugbrain.mcp_bridge.config import _SCRIPTS_DIR, BridgeUnavailableError

logger = logging.getLogger(__name__)


def _ecosystem_to_lang(ecosystem: str) -> str:
    """Map MCP ecosystem tags to RAG language tags."""
    mapping = {
        "EVM": "solidity",
        "Solana": "rust",
        "Cosmos": "cosmwasm",
        "ZK": "circom",
        "AI": "python",
    }
    return mapping.get(ecosystem, "solidity")


def _slugify(text: str) -> str:
    """Slugify a pattern title for use as a stable rel_key component."""
    return "".join(c if c.isalnum() else "-" for c in (text or "").lower()).strip("-")[:80]


async def sync_security_patterns(limit: int = 100, dry_run: bool = False) -> dict[str, Any]:
    """Pull MCP ChromaDB security patterns into the RAG ``coding_knowledge``.

    Lists patterns from the MCP (``list_patterns``), converts each into a
    coding-knowledge doc (title + description + code block), and ingests them
    into the ``coding_knowledge`` collection via the RAG's own store so the
    RAG can answer security questions even when the MCP is offline.

    Returns ingestion stats; ``dry_run=True`` only reports the doc count.
    """
    from rugbrain import mcp_bridge as _pkg

    try:
        raw = await _pkg._call_mcp("patterns", {"limit": min(limit, 100)})
    except BridgeUnavailableError as exc:
        logger.info("sync_security_patterns MCP unavailable: %s", str(exc)[:120])
        return {"ok": False, "bridge": "unavailable", "error": str(exc), "ingested": 0}

    patterns = raw.get("patterns", []) or []
    docs: list[dict[str, Any]] = []
    for p in patterns:
        title = p.get("title", "Untitled")
        description = p.get("description", "")
        code = p.get("code_block", "") if "code_block" in p else ""
        content = f"{title}. {description}"
        if code:
            content += f"\n```\n{code}\n```"
        docs.append(
            {
                "title": title,
                "content": content,
                "ecosystem": _ecosystem_to_lang(p.get("ecosystem", "")),
                "category": p.get("category", "security_pattern"),
                "severity": p.get("severity", "medium"),
            }
        )

    if not docs:
        return {"ok": True, "bridge": "mcp", "ingested": 0, "patterns": 0, "dry_run": dry_run}

    if dry_run:
        return {
            "ok": True,
            "bridge": "mcp",
            "ingested": 0,
            "patterns": len(docs),
            "dry_run": True,
        }

    try:
        # Reuse the RAG's own store + the shared coding-doc builder.
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))
        import httpx
        from coding_ingest_common import build_coding_doc, ingest_coding_docs

        from rugbrain.qdrant_store import QdrantVectorStore

        coding_docs: list[dict[str, Any]] = []
        for d in docs:
            coding_docs.append(
                build_coding_doc(
                    source="mcp-security-patterns",
                    rel_key=f"mcp/{_slugify(d['title'])}",
                    content=d["content"],
                    ecosystem=d["ecosystem"],
                    language=d["ecosystem"],
                    knowledge_type="security_pattern",
                    topic=d["category"],
                    title=d["title"],
                    severity=d["severity"],
                )
            )
        async with httpx.AsyncClient(timeout=120.0) as client:
            store = QdrantVectorStore()
            try:
                stats = await ingest_coding_docs(
                    client,
                    store,
                    coding_docs,
                    source="mcp-security-patterns",
                    dry_run=False,
                )
            finally:
                await store.close()
        return {
            "ok": True,
            "bridge": "mcp",
            "ingested": int(stats.get("inserted", 0)),
            "patterns": len(docs),
            "chunks": int(stats.get("chunks", 0)),
            "failed": int(stats.get("failed", 0)),
            "dry_run": False,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("sync_security_patterns ingest failed err=%s", str(exc)[:200])
        return {"ok": False, "bridge": "mcp", "error": str(exc)[:200], "ingested": 0}
