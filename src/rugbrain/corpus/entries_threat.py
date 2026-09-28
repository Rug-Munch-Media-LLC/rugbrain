"""Declared corpora, group: threat-intel and audits.

Audit findings, published audits, the security-pattern corpora and the live signal feeds
(threats, scan intel, security alerts, security feeds). The largest group in the fleet by
collection count.

Every row is transcribed from ``docs/COVERAGE-AUDIT-2026-09-28.md`` §1 (96 collections,
6,314,262 points, dims {1024, 2560, 4096}). LANGUAGE and DOMAIN are what this file adds:
the audit measured the collections but never said which axis a repo belonged to, which is
how the "this is a Solidity corpus" belief survived. See ``corpus/entries.py`` for the add
path and ``corpus/schema.py`` for what each field means.
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.entries_base import DAILY, HOURLY, ON_DEMAND, WEEKLY, D, L, _d
from rugbrain.corpus.schema import (
    LICENCE_PERMISSIVE,
    LICENCE_PUBLIC_DOMAIN,
    CorpusDeclaration,
)

_THREAT: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "hacks-v2",
        "hacks_v2",
        cid="hacks_v2",
        language=L.SOLIDITY,
        domain=D.EXPLOIT,
        url="https://github.com/sunweb3sec/DeFiHackLabs",
        licence=LICENCE_PUBLIC_DOMAIN,
        cadence=WEEKLY,
        notes="8 pts, 29d STALE (audit §5.1). MIGRATED — base 2560, resolver rewrites "
        "writes to the 4096 twin.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "contract-audits",
        "contract_audits",
        cid="contract_audits",
        language=L.MARKDOWN,
        domain=D.THREAT_INTEL,
        url="https://github.com/yAudit/defi-fork-bugs",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="902 pts. 1,101 audit-prose docs fleet-wide is all the paper-shaped "
        "content the fleet holds (audit §3.1).",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "github-audits",
        "github_audits",
        cid="github_audits",
        language=L.MARKDOWN,
        domain=D.THREAT_INTEL,
        url="https://github.com/trailofbits/publications",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="199 pts.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "contract-security",
        "contract_security",
        cid="contract_security",
        language=L.MARKDOWN,
        domain=D.THREAT_INTEL,
        url="internal://rag/csec",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="1,099 pts.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "audit-findings",
        "audit_findings",
        cid="audit_findings",
        language=L.MARKDOWN,
        domain=D.THREAT_INTEL,
        url="https://github.com/crytic",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="0 points, never fed.",
        observed=2560,
        retired=()
    ),
    _d(
        "security-patterns-owasp-web3",
        "security_patterns",
        cid="security_patterns",
        language=L.PYTHON,
        domain=D.THREAT_INTEL,
        url="internal://contextcrypto/owasp-web3-security",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="2,008 pts. Web3 patterns, not contract source. Base 2560, twin is the 4096 lane.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "security-patterns-test-fixture",
        "security_patterns_test",
        cid="security_patterns_test",
        language=L.PYTHON,
        domain=D.THREAT_INTEL,
        url="internal://fixture/security-patterns",
        licence=LICENCE_PERMISSIVE,
        cadence=ON_DEMAND,
        notes="92 pts. Fixture, not a corpus.",
        observed=2560,
    ),
    _d(
        "live-threat-signals",
        "live_threats",
        cid="live_threats",
        language=L.DATA,
        domain=D.THREAT_INTEL,
        url="internal://pipe/threats",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="11,164 pts.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "threat-intel-v2",
        "threat_intel_v2",
        cid="threat_intel_v2",
        language=L.DATA,
        domain=D.THREAT_INTEL,
        url="internal://pipe/intel",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="41,708 pts. MIGRATED — base 2560, resolver rewrites writes to the 4096 _8b twin.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "scan-intel",
        "scan_intel",
        cid="scan_intel",
        language=L.DATA,
        domain=D.THREAT_INTEL,
        url="internal://pipe/scan",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="46,745 base at 2560 / 0 twin (audit §1.3) — canonical lane sees nothing.",
        observed=2560,
    ),
    _d(
        "security-alerts",
        "security_alerts",
        cid="security_alerts",
        language=L.DATA,
        domain=D.THREAT_INTEL,
        url="internal://pipe/alerts",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="125 pts.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "security-feeds",
        "security_feeds",
        cid="security_feeds",
        language=L.DATA,
        domain=D.THREAT_INTEL,
        url="internal://pipe/feeds",
        licence=LICENCE_PERMISSIVE,
        cadence=HOURLY,
        notes="874 pts.",
        observed=2560,
        retired=(2560,),
    ),
)

__all__ = [
    "_THREAT",
]
