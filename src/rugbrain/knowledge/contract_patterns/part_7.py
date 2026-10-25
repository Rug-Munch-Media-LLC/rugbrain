"""Chunk 7 of ``SECURITY_PATTERNS`` (generated; literal data only).

Source: scripts/contract_knowledge_data.py
"""

ITEMS = [
    {
            "pattern_id": "sol:rent-exemption",
            "title": "Missing rent-exemption check (account lifecycle)",
            "ecosystem": "solana",
            "category": "input-validation",
            "swc": "",
            "severity": "medium",
            "vulnerable_code": (
                "// Account created without ensuring rent-exempt balance; if\n"
                "// lamports drop below rent, the account can be deleted/reaped\n"
                "// and state lost."
            ),
            "fixed_code": (
                "// Anchor: init / init_if_needed ensures rent exemption; or\n"
                "// require!(account.lamports() >= rent.minimum_balance(data_len));"
            ),
            "description": (
                "Non-rent-exempt accounts can be removed by the runtime, losing "
                "state. Create accounts with rent-exempt lamports (Anchor init "
                "handles this) and check lamports for externally-created accounts."
            ),
            "detection_heuristics": [
                "create_account without lamports >= minimum rent",
                "no rent-exemption check on mutable accounts",
            ],
            "references": ["https://docs.solana.com/developing/programming-model/accounts"],
        },
]
