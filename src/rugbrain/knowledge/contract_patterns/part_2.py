"""Chunk 2 of ``AUDIT_FINDINGS`` (generated; literal data only).

Source: scripts/contract_knowledge_data.py
"""

ITEMS = [
    {
            "finding_id": "audit:radiant-2024",
            "title": "Radiant Capital — rounding/accounting exploit ($58M)",
            "ecosystem": "ethereum",
            "category": "business-logic",
            "severity": "high",
            "pattern_class": "rounding / accounting precision in borrow accounting",
            "description": (
                "January 2024 (v2, BSC/ARB ~$4.5M) and October 2024 (~$58M): "
                "Radiant's lending pool accounting was exploited via rounding "
                "precision / inconsistent share accounting in the "
                "deposit/borrow path, allowing repeated mint/redemption of value."
            ),
            "detection_heuristics": [
                "share/accounting math with truncation that can be looped",
                "borrow/repay paths using inconsistent precision",
            ],
            "remediation": "Use high-precision internal accounting, virtual shares, and invariant tests over rounding.",
            "reference_url": "https://rekt.news/radiant-capital-rekt/",
            "amount_lost_usd": 58_000_000,
        },
    {
            "finding_id": "audit:eigen-2025",
            "title": "EigenLayer EIGEN — governance token theft ($5.5M)",
            "ecosystem": "ethereum",
            "category": "access-control",
            "severity": "high",
            "pattern_class": "governance token migration vulnerability",
            "description": (
                "April 2025: ~1.67M EIGEN (~$5.5M) was stolen from the EIGEN "
                "token contract via a vulnerability in the token's "
                "governance/migration logic, highlighting that even 'security "
                "primitives' ecosystems must audit token upgrade paths."
            ),
            "detection_heuristics": [
                "token migration/redeem functions with weak authorization",
                "upgradeable token logic reachable without timelock",
            ],
            "remediation": "Audit token upgrade/migration paths, gate privileged functions behind timelock + multisig.",
            "reference_url": "https://www.theblock.co/post/345761/eigenlayer-eigen-token-theft",
            "amount_lost_usd": 5_500_000,
        },
    {
            "finding_id": "audit:nomad-2022",
            "title": "Nomad bridge — trusted root optimization bug ($190M)",
            "ecosystem": "ethereum",
            "category": "input-validation",
            "severity": "critical",
            "pattern_class": "incorrect root initialization allowing message forgery",
            "description": (
                "August 2022: Nomad's trusted root was initialized to "
                "bytes32(0) (the zero hash), so any message with a zero-hashed "
                "root validated — enabling copy-paste message forgery that "
                "drained ~$190M within hours."
            ),
            "detection_heuristics": [
                "root/commitment initialized to zero and accepted in verification",
                "verification accepts default/empty values as valid",
            ],
            "remediation": "Never allow zero/empty commitments in verification; initialize trusted roots at deploy; add replay protection.",
            "reference_url": "https://rekt.news/nomad-rekt/",
            "amount_lost_usd": 190_000_000,
        },
]
