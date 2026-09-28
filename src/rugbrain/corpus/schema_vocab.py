"""The closed vocabularies: language, domain and licence tokens.

Split from ``schema`` so the declaration shape and the axes it is constrained to
can be read, reviewed and extended independently. Both are under the repo's
200-line cap only while they live apart, and the whole point of the package is
that these lists are closed — so they are the thing most worth reading in full.

WHY CLOSED
    The operator asked for corpora to be categorised BEFORE embed "so there is no
    confusion sorting nonsense". A free-text axis cannot be enforced; a closed one
    can: an uncategorised corpus is literally undeclinable, and ``gate`` refuses a
    ``code`` corpus whose language is ``unclassified``.

THE LANGUAGES ARE THE MEASURED ONES
    The list carries the head of the measured distribution rather than the team's
    prior: rust 24.38%, go 17.44%, typescript 9.86%, solidity 7.78%, python 5.24%,
    c 1.03%, javascript 0.83%, markdown 0.81% (audit §2), plus the tail that
    matters for a crypto fleet (java, move) and the non-code carriers (docs, data).
    Solidity is present and it is FIFTH.

    It deliberately omits languages the audit measured below 0.05% as one-offs
    (cairo, haskell, lua, vyper, circom). They are real and they are indexed, but
    a vocab is a contract: adding a language is a deliberate act with a measured
    count behind it, not an accident of ingestion. A repo in one of them declares
    ``unclassified`` until someone looks at it.

THE DOMAINS ARE WHAT THE CORPUS IS FOR
    Not the same question as what language it is written in. That distinction is
    the whole finding: the Solidity repos (DeFiHackLabs, SWC-registry,
    solana-program-library) feed the EXPLOIT pipeline, while the 885-library code
    corpus is Rust/Go/TypeScript. The two were conflated for weeks because nothing
    recorded the axis.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

#: Open-vocabulary marker. A corpus that cannot honestly be placed is a finding
#: to resolve, not a licence to invent a label.
UNCLASSIFIED: Final[str] = "unclassified"


class CorpusLanguage(StrEnum):
    """Closed vocabulary for the LANGUAGE axis.

    Covers every head language in the measured corpus (rust 24.38%, go 17.44%,
    typescript 9.86%, solidity 7.78%, python 5.24%, c 1.03%, javascript 0.83%,
    markdown 0.81% — ``docs/COVERAGE-AUDIT-2026-09-28.md`` §2) plus the long
    tail the same table records (java, move, plus the non-code carriers: docs
    and data). ``unclassified`` is a valid DECLARED value — it is what a
    genuinely mixed corpus says — but it never satisfies the gate for a code
    corpus, so a mis-categorised repo cannot quietly pass.
    """

    PYTHON = "python"
    JAVA = "java"
    RUST = "rust"
    GO = "go"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    SOLIDITY = "solidity"
    C = "c"
    MOVE = "move"
    MARKDOWN = "markdown"
    DOCS = "docs"
    DATA = "data"
    UNCLASSIFIED = UNCLASSIFIED


class CorpusDomain(StrEnum):
    """Closed vocabulary for the DOMAIN axis.

    The eight the operator named, plus ``unclassified``. A domain names what
    the corpus is FOR, which is not the same question as what language it is
    written in: the Solidity repos in this fleet feed the exploit pipeline
    (DeFiHackLabs, SWC-registry, solana-program-library), while the 885-library
    code corpus is dominated by Rust, Go and TypeScript. The two were conflated
    for weeks. ``domain`` is what keeps them apart.
    """

    CODE = "code"
    KNOWLEDGE = "knowledge"
    THREAT_INTEL = "threat-intel"
    EXPLOIT = "exploit"
    SCAM = "scam"
    MARKET = "market"
    NEWS = "news"
    WALLET_LABEL = "wallet-label"
    UNCLASSIFIED = UNCLASSIFIED


#: Languages a ``code``-domain corpus may declare. A code corpus in a language
#: outside this set is a categorisation error, not a new language.
CODE_LANGUAGES: Final[frozenset[CorpusLanguage]] = frozenset(
    {
        CorpusLanguage.PYTHON,
        CorpusLanguage.JAVA,
        CorpusLanguage.RUST,
        CorpusLanguage.GO,
        CorpusLanguage.TYPESCRIPT,
        CorpusLanguage.JAVASCRIPT,
        CorpusLanguage.SOLIDITY,
        CorpusLanguage.C,
        CorpusLanguage.MOVE,
    }
)

#: Declared licence tokens. ``UNLICENSED`` and ``NOASSERTION`` are USABLE under
#: the operator ruling (internal, private, nothing sold — the test is
#: redistribution), and are recorded verbatim so a product-bound day can find
#: them. See ``licence_status()`` for what each one resolves to.
LICENCE_PUBLIC_DOMAIN: Final = "public-domain"
LICENCE_PERMISSIVE: Final = "permissive"
LICENCE_COPYLEFT: Final = "copyleft"
LICENCE_NON_COMMERCIAL: Final = "non-commercial"
LICENCE_GATED: Final = "gated"
LICENCE_UNLICENSED: Final = "unlicensed"
LICENCE_NOASSERTION: Final = "noassertion"

#: Licences that grant redistribution rights. Anything else is still usable
#: internally, but must be re-reviewed before a product ships it.
REDISTRIBUTABLE_LICENCES: Final[frozenset[str]] = frozenset(
    {LICENCE_PUBLIC_DOMAIN, LICENCE_PERMISSIVE}
)


class LicenceStatus(StrEnum):
    """What a declared licence MEANS for this corpus, under the internal-use ruling.

    ``INTERNAL_ONLY`` deliberately covers three distinct situations that a
    product-bound day must be able to tell apart: no licence at all, an explicit
    non-commercial clause, and copyleft. The verdict is the same today — usable,
    because nothing is redistributed — and the DECLARED LICENCE STRING on the
    entry is what distinguishes them later. Keeping them in one status is the
    cost of not wanting a licence parser to be a second source of truth.
    """

    REDISTRIBUTABLE = "redistributable"
    INTERNAL_ONLY = "internal-only"
    GATED_ACCESS = "gated-access"
    UNKNOWN = "unknown"

__all__ = [
    "CODE_LANGUAGES",
    "LICENCE_COPYLEFT",
    "LICENCE_GATED",
    "LICENCE_NOASSERTION",
    "LICENCE_NON_COMMERCIAL",
    "LICENCE_PERMISSIVE",
    "LICENCE_PUBLIC_DOMAIN",
    "LICENCE_UNLICENSED",
    "REDISTRIBUTABLE_LICENCES",
    "UNCLASSIFIED",
    "CorpusDomain",
    "CorpusLanguage",
    "LicenceStatus",
]
