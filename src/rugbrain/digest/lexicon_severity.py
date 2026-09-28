"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Severity word list — the lexicon behind :mod:`rugbrain.digest.severity`.

Ordered: the first hit wins, so the most specific comes first. These are
deliberately blunt. Severity drives which Qdrant collection a document
lands in, so a false "critical" is costlier than a false "low".
"""

from __future__ import annotations

from typing import Final

SEVERITY_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "critical",
        (
            "rug pull",
            "rugpull",
            "rug pulled",
            "drainer",
            "drainers",
            "honeypot",
            "private key compromised",
            "private key leak",
            "exploit",
            "exploited",
            "drained",
            "drain ",
            "stolen funds",
            "compromised",
            "breach",
            "exit scam",
            "insider",
        ),
    ),
    (
        "high",
        (
            "phishing",
            "scam",
            "scammed",
            "laundering",
            "sanction",
            "arrest",
            "seized",
            "impersonat",
            "blackmail",
        ),
    ),
    (
        "medium",
        (
            "vulnerability",
            "vuln",
            "attack",
            "suspected",
            "warning",
            "malicious",
            "outage",
            "freeze",
        ),
    ),
)
