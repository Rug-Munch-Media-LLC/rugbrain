"""Specialized multi-head embedding methods."""

from __future__ import annotations

import json
from typing import Any

from rugbrain.feature_extractors import (
    extract_contract_features,
    extract_transaction_features,
    extract_wallet_features,
)

from .result import EmbeddingResult


class HeadsMixin:
    """Token / wallet / scam-pattern / news / query embedding heads."""

    async def _semantic_embed_one(self, text: str, head: str = "semantic") -> list[float]:
        raise NotImplementedError

    async def embed_token_scam(
        self,
        name: str,
        symbol: str,
        description: str = "",
        contract_code: str = "",
        creator_address: str = "",
        chain: str = "solana",
        metadata: dict[str, Any] | None = None,
    ) -> EmbeddingResult:
        """
        Generate a comprehensive token scam embedding.
        Combines semantic, code, and behavioral vectors.
        """
        # Build rich semantic text
        semantic_text = f"""Token Analysis: {name} ({symbol}) on {chain}.
Description: {description[:2000]}
Creator: {creator_address}
Metadata: {json.dumps(metadata or {})[:1000]}"""

        # Get semantic embedding
        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")
        semantic_dim = len(semantic_vec)

        # Get code features
        code_vec = (
            extract_contract_features(contract_code).tolist() if contract_code else [0.0] * 128
        )

        # Get behavioral features (from metadata)
        behavioral = extract_transaction_features(metadata or {})
        behavioral_vec = behavioral.tolist()

        # Get wallet features for creator
        wallet = extract_wallet_features(
            {
                "address": creator_address,
                "labels": metadata.get("creator_labels", []) if metadata else [],
            }
        )
        wallet_vec = wallet.tolist()

        # Concatenate: semantic (d) + code (128) + behavioral (64) + wallet (64)
        combined = semantic_vec + code_vec + behavioral_vec + wallet_vec

        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="token_scam",
            metadata={
                "name": name,
                "symbol": symbol,
                "chain": chain,
                "semantic_dim": semantic_dim,
                "has_code": bool(contract_code),
                "has_behavioral": bool(metadata),
            },
        )

    async def embed_wallet(
        self,
        address: str,
        transactions: list[dict[str, Any]] | None = None,
        labels: list[str] | None = None,
        chain: str = "solana",
        balance_usd: float = 0,
    ) -> EmbeddingResult:
        """
        Generate a comprehensive wallet risk embedding.
        """
        wallet_data = {
            "address": address,
            "labels": labels or [],
            "balance_usd": balance_usd,
            "transaction_count": len(transactions or []),
        }
        wallet_vec = extract_wallet_features(wallet_data).tolist()

        behavioral_vec = extract_transaction_features({"transactions": transactions or []}).tolist()

        # Semantic: labels + chain context
        semantic_text = f"Wallet {address} on {chain}. Labels: {', '.join(labels or ['unknown'])}. Activity: {len(transactions or [])} transactions."
        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")

        combined = semantic_vec + wallet_vec + behavioral_vec

        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="wallet",
            metadata={
                "address": address,
                "chain": chain,
                "labels": labels or [],
                "tx_count": len(transactions or []),
            },
        )

    async def embed_scam_pattern(
        self,
        pattern_name: str,
        description: str,
        code_snippets: list[str] | None = None,
        indicators: list[str] | None = None,
        severity: str = "high",
    ) -> EmbeddingResult:
        """
        Embed a known scam pattern for similarity matching.
        """
        semantic_text = f"""SCAM PATTERN: {pattern_name}
Severity: {severity}
Description: {description}
Indicators: {", ".join(indicators or [])}
Code patterns: {"; ".join(code_snippets or [])[:3000]}"""

        semantic_vec = await self._semantic_embed_one(semantic_text, "semantic")
        len(semantic_vec)

        # Code features from snippets
        all_code = "\n".join(code_snippets or [])
        code_vec = extract_contract_features(all_code).tolist()

        combined = semantic_vec + code_vec

        return EmbeddingResult(
            vector=combined,
            dims=len(combined),
            model="crypto_multihead_v1",
            head="scam_pattern",
            metadata={
                "pattern": pattern_name,
                "severity": severity,
                "indicators": indicators or [],
            },
        )

    async def embed_news(
        self,
        title: str,
        content: str,
        source: str = "",
        tags: list[str] | None = None,
    ) -> EmbeddingResult:
        """News article embedding for market intel."""
        text = f"TITLE: {title}\nSOURCE: {source}\nTAGS: {', '.join(tags or [])}\nCONTENT: {content[:3000]}"
        vec = await self._semantic_embed_one(text, "semantic")

        return EmbeddingResult(
            vector=vec,
            dims=len(vec),
            model="openai/text-embedding-3-large",
            head="news",
            metadata={"title": title, "source": source, "tags": tags or []},
        )

    async def embed_query(self, query: str) -> list[float]:
        """Embed a search query for RAG retrieval.

        Uses the official Qwen3-Embedding ``Instruct:`` query template so
        queries are embedded in the same space as the stored documents.
        """
        from ._query_instruct import _QUERY_INSTRUCT as _QUERY_INSTRUCT

        augmented = f"Cryptocurrency scam detection query: {query}"
        instructed = _QUERY_INSTRUCT.format(augmented)
        return await self._semantic_embed_one(instructed, "semantic")
