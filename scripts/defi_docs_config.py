"""Declared DeFi protocol documentation repos: identity, licence class, page bounds.

One job — hold the DECLARATION (which repos, which pages count, what a licence
means for redistribution). Fetching, chunking and embedding live elsewhere.

Repo URLs were verified with `git ls-remote` on 2026-10-25. Three of the five
previously declared URLs did NOT exist (`aave/aave-docs`, `curvefi/curve-docs`,
`balancer/balancer-docs`, `lidofinance/lido-docs` all 404), which is why the run
recorded "no docs repo clonable" for every protocol. A non-existent repo is a
declaration bug, not a runtime failure.
"""

from __future__ import annotations

import re

CAP_PAGES = 250
CAP_CHUNKS = 350
MAX_SECTION = 2600
MIN_BODY = 400

# Verified clones come first. A failed candidate is recorded, never silently skipped.
REPOS = {
    "uniswap": {
        "candidates": ["https://github.com/Uniswap/docs"],
        "site": "https://docs.uniswap.org",
    },
    "aave": {
        "candidates": [
            "https://github.com/aave/aave-docs",
            "https://github.com/aave/docs",
            "https://github.com/aave/aave-v3-core",
        ],
        "site": "https://aave.com/docs",
    },
    "curve": {
        "candidates": ["https://github.com/curvefi/docs", "https://github.com/curvefi/curve-docs"],
        "site": "https://docs.curve.fi",
    },
    "balancer": {
        "candidates": ["https://github.com/balancer/docs", "https://github.com/balancer/balancer-docs"],
        "site": "https://docs.balancer.fi",
    },
    "lido": {
        "candidates": ["https://github.com/lidofinance/docs", "https://github.com/lidofinance/lido-docs"],
        "site": "https://docs.lido.fi",
    },
}

EXCLUDE = re.compile(
    r"(^|/)(node_modules|\.git|\.github|i18n|locales?|translations?|blog|changelog|"
    r"release-notes|releases|archive|_snippets|static|assets|images?|public)/"
    r"|(^|/)(CHANGELOG|CHANGES|CONTRIBUTING|CODE_OF_CONDUCT|LICENSE|README[-_]?i18n)",
    re.IGNORECASE,
)
KEEP_HINT = re.compile(
    r"(concept|guide|core|protocol|overview|architecture|math|mechan|contract|"
    r"liquidity|pool|tick|range|oracle|position|borrow|supply|liquidat|health|"
    r"collateral|invariant|stableswap|amplif|rebase|steth|staking|withdraw|"
    r"swap|fee|flash|governance|whitepaper)",
    re.IGNORECASE,
)

LICENCE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "COPYING")
SPDX_SOURCES = ("package.json", "pyproject.toml", "Cargo.toml")

# Text-licence detection: (pattern, spdx, publishable, note).
LICENCES = [
    (r"\bMIT License\b|\bPermission is hereby granted, free of charge\b", "MIT", True, "attribution"),
    (r"Business Source License|\bBUSL-1\.1\b", "BUSL-1.1", False, "source-available: internal use only"),
    (r"Apache License,?\s+Version 2\.0", "Apache-2.0", True, "attribution + NOTICE"),
    (r"\bCC0 1\.0\b|Creative Commons Zero", "CC0-1.0", True, "public domain"),
    (r"\bCC[- ]BY[- ]4\.0\b|Attribution 4\.0 International", "CC-BY-4.0", True, "attribution required"),
    (r"BSD 3-Clause|Redistribution and use in source and binary", "BSD-3-Clause", True, "attribution"),
    (r"GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0-only", False, "copyleft: internal use only"),
    (r"GNU GENERAL PUBLIC LICENSE", "GPL-3.0-only", False, "copyleft: internal use only"),
    (r"\bUNLICENSED\b|All rights reserved|proprietary", "Proprietary-docs", False, "no redistribution"),
]

# SPDX identifier -> publishable. Unknown identifiers are NOT permissive.
SPDX_PUBLISHABLE = {
    "MIT": True, "Apache-2.0": True, "BSD-3-Clause": True, "BSD-2-Clause": True,
    "CC0-1.0": True, "CC-BY-4.0": True, "ISC": True, "Unlicense": True,
    "GPL-3.0-only": False, "GPL-3.0-or-later": False, "AGPL-3.0-only": False,
    "AGPL-3.0-or-later": False, "LGPL-3.0-only": False, "BUSL-1.1": False,
    "UNLICENSED": False, "CC-BY-SA-4.0": False, "CC-BY-NC-4.0": False,
}


def classify_licence(text: str) -> tuple[str, bool, str]:
    """(spdx, publishable, note) — unknown text is never treated as permissive."""
    for pattern, spdx, publishable, note in LICENCES:
        if re.search(pattern, text, re.IGNORECASE):
            return spdx, publishable, note
    return "UNKNOWN", False, "licence not recognised: internal use only"


def spdx_from_text(raw: str) -> str | None:
    """SPDX id from package.json / pyproject.toml / Cargo.toml."""
    m = re.search(r'"license"\s*:\s*"([^"]{2,64})"', raw)
    if not m:
        m = re.search(r'^\s*license\s*=\s*[{]?[^}\n]*?"?([A-Za-z0-9.\-+]{2,64})"?', raw, re.M)
    return m.group(1).strip() if m else None


def classify_spdx(spdx: str) -> tuple[str, bool, str]:
    publishable = SPDX_PUBLISHABLE.get(spdx)
    if publishable is None:
        return spdx, False, "spdx not recognised: internal use only"
    return spdx, publishable, ("permits redistribution" if publishable else "restricted: internal use only")


def is_candidate(path: str) -> bool:
    if not path.lower().endswith((".md", ".mdx")):
        return False
    return not EXCLUDE.search(path)


def rank(path: str) -> tuple[int, int, str]:
    """Deterministic order: mechanics-relevant pages first, then shorter paths."""
    return (0 if KEEP_HINT.search(path) else 1, path.count("/"), path)


def pick_pages(paths: list[str], cap: int = CAP_PAGES) -> list[str]:
    """Bounded, deterministic selection. Excluded pages are counted by the caller."""
    kept = sorted((p for p in paths if is_candidate(p)), key=rank)
    return kept[:cap]
