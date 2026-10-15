"""Declared corpora, group: code.

The 885-library code corpus. The exploit pipeline the Solidity repos actually feed is
``entries_exploit``; these two are separate because they answer different questions.

Every row is transcribed from ``docs/COVERAGE-AUDIT-2026-09-28.md`` §1 (96 collections,
6,314,262 points, dims {1024, 2560, 4096}). LANGUAGE and DOMAIN are what this file adds:
the audit measured the collections but never said which axis a repo belonged to, which is
how the Solidity-repos-are-the-code-corpus belief survived. See ``corpus/entries.py``.

See ``corpus/schema.py`` for what each field means and ``corpus/gate.py`` for the refusal.
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.entries_base import ON_DEMAND, WEEKLY, D, L, _d
from rugbrain.corpus.schema import (
    LICENCE_PERMISSIVE,
    CorpusDeclaration,
)

_CODE: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "code-corpus-885-libraries",
        "codebase_reference_v2",
        cid="codebase_reference_v2",
        language=L.RUST,
        domain=D.CODE,
        url="internal://zoekt/rmi-codebase-mcp-corpus",
        licence=LICENCE_PERMISSIVE,
        cadence=ON_DEMAND,
        notes=(
            "2,130,842 pts (4,257,472 with twin). 885 libraries. Dominant language rust "
            "24.38%, then go 17.44%, typescript 9.86%, solidity 7.78%, python 5.24%. "
            "Language field names the DOMINANT language of the corpus, not every "
            "language it contains. BLIND freshness (audit 5.2). MEASURED LIVE 2560: this "
            "BASE name is 2560 and its _8b twin is the 4096 serving lane, so dim=4096 "
            "with observed=2560 is the documented no-overwrite split, not a contradiction."
        ),
        observed=2560,
        retired=(),
    ),
    _d(
        "coding-lessons-good-bad",
        "coding_knowledge",
        cid="coding_knowledge",
        language=L.MARKDOWN,
        domain=D.KNOWLEDGE,
        url="internal://lessons/rmi-coding-findings",
        licence=LICENCE_PERMISSIVE,
        cadence=ON_DEMAND,
        notes="2,440 house findings. 0 points, never fed. Base 2560, twin is the "
        "4096 serving lane (lane_table owns the name), so retired=(2560,).",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "our-good-bad-exemplars",
        "internal-lessons",
        cid="internal-lessons",
        language=L.PYTHON,
        domain=D.KNOWLEDGE,
        url="internal://lessons/exemplars",
        licence=LICENCE_PERMISSIVE,
        cadence=ON_DEMAND,
        notes="43 curated GOOD/BAD exemplars, 6 languages.",
    ),
    _d(
        "sourcify-verified-contracts",
        "sourcify_contracts",
        cid="sourcify_contracts",
        language=L.SOLIDITY,
        domain=D.CODE,
        url="internal://pipe/sourcify",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
    ),
    _d(
        "solidity-patterns",
        "solidity_patterns",
        cid="solidity_patterns",
        language=L.SOLIDITY,
        domain=D.CODE,
        url="internal://pipe/solidity-trivia",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="Owned name with NO Qdrant collection (lane_table).",
    ),
    _d(
        "zk-circuits",
        "zk_circuits",
        cid="zk_circuits",
        language=L.SOLIDITY,
        domain=D.CODE,
        url="internal://pipe/zk-circuits",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="circom/aiken/sway/ligo — the 0.02-0.03% tail (audit §2).",
        observed=4096,
        retired=(),
    ),
)

__all__ = [
    "_CODE",
]
