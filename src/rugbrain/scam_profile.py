"""Normalize raw scanner token data into the pattern-matching engine's shape.

The scanner and backend each send a slightly different set of field names for
the same facts -- nested ``metadata`` vs top-level keys, ``contract_code`` vs
``code``, ``creator_address`` vs ``deployer_address``. This module folds all of
those variants into one dict so the matching engine compares against a single
shape instead of probing for aliases.
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_token_scam_profile"]


def build_token_scam_profile(token_data: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized scam-analysis profile from raw scanner token data.

    Normalizes the many field shapes the scanner/backend can send (nested
    ``metadata``, top-level keys, ``contract_code`` vs ``code``) into one
    dict consumed by the pattern-matching engine.
    """
    metadata = token_data.get("metadata") or {}
    merged = {**metadata, **{k: v for k, v in token_data.items() if k != "metadata"}}
    return {
        "address": merged.get("address") or merged.get("token_address") or "",
        "chain": merged.get("chain") or "ethereum",
        "name": merged.get("name") or merged.get("token_name") or "",
        "symbol": merged.get("symbol") or merged.get("token_symbol") or "",
        "contract_code": merged.get("contract_code")
        or merged.get("code")
        or merged.get("source_code")
        or "",
        "description": merged.get("description") or merged.get("summary") or "",
        "deployer_address": merged.get("deployer_address") or merged.get("creator_address") or "",
        "deployer_metrics": {
            "deployment_count": merged.get("deployment_count") or merged.get("deploy_count") or 0,
            "mixer_funded": bool(merged.get("mixer_funded") or merged.get("funded_by_mixer")),
            "rapid_deploy": bool(merged.get("rapid_deploy") or merged.get("rapid_deployment")),
            "multi_chain": bool(
                merged.get("multi_chain") or merged.get("deployed_multiple_chains")
            ),
            "age_hours": merged.get("age_hours") or merged.get("token_age_hours") or 0,
        },
        "economics": {
            "sell_tax_pct": merged.get("sell_tax_pct")
            or merged.get("sell_tax")
            or merged.get("tax")
            or 0,
            "lp_locked": bool(merged.get("lp_locked")),
            "mint_enabled": bool(merged.get("mint_enabled") or merged.get("can_mint")),
            "ownership_renounced": bool(merged.get("ownership_renounced")),
            "holder_concentration_pct": merged.get("holder_concentration_pct")
            or merged.get("top10_holder_pct")
            or 0,
        },
    }
