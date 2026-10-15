"""RugBrain fact-check gate — per-claim verdicts for a draft.

Four deterministic steps (design: docs/FACT-CHECK-GATE.md):

  1. **extract**  — pull verifiable claims from the draft text
     (addresses, tx hashes, amounts, percentages, protocol names).
  2. **bind**     — match each claim to the sources that mention it.
  3. **corroborate** — a claim passes when >= 2 *independent* sources
     (distinct domains) support it.
  4. **verdict**  — per-claim ``pass`` / ``flag`` plus a draft-level gate.

Pure Python (stdlib + the existing ``news_rating`` claim extractor): no
network, no embeddings, deterministic and testable. The embed/rerank stack
is deliberately NOT used here — claim-source binding is exact-token
matching, which is reproducible and cheap; a semantic rung can be layered
on later behind the same ``bind_claims`` seam.

Usage::

    from rugbrain.factcheck import check_draft

    report = check_draft(draft_text, sources)
    print(report["verdict"], report["claims"])
"""

from __future__ import annotations

import re
from typing import Any

from rugbrain.news_rating.factual import _find_claims
from rugbrain.news_rating.sources import domain_from_url

#: A claim passes when this many independent domains support it.
MIN_INDEPENDENT_SOURCES = 2

#: Claim categories that carry a verifiable, checkable value.
_VERIFIABLE = (
    "addresses",
    "tx_hashes",
    "solana_addresses",
    "amounts_usd",
    "amounts_token",
    "percentages",
)


def _normalise(value: str) -> str:
    """Case/space-insensitive key so '0xAB..' and '0xab..' bind alike."""
    return re.sub(r"\s+", "", value.strip().lower())


def extract_claims(draft: str) -> list[dict[str, Any]]:
    """Step 1 — return the draft's verifiable claims as flat records.

    Each record is ``{"category", "value", "key"}``; ``key`` is the
    normalised form used for source binding. Protocol names are excluded
    (they are context, not a checkable value).
    """
    found = _find_claims(draft or "")
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category in _VERIFIABLE:
        for value in found.get(category, []):
            key = _normalise(value)
            if not key or key in seen:
                continue
            seen.add(key)
            claims.append({"category": category, "value": value, "key": key})
    return claims


def _source_text(source: dict[str, Any]) -> str:
    """Concatenate the searchable text of one source record."""
    parts = [
        str(source.get("title") or ""),
        str(source.get("content") or ""),
        str(source.get("snippet") or ""),
    ]
    return " ".join(p for p in parts if p)


def _source_domain(source: dict[str, Any]) -> str:
    """Resolve a source's domain from url/source fields ('' if unknown)."""
    for field in ("url", "source_url", "source"):
        raw = source.get(field)
        if isinstance(raw, str) and raw.strip():
            domain = domain_from_url(raw)
            if domain:
                return domain
    return ""


def bind_claims(
    claims: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Step 2 — attach the sources that mention each claim.

    A source supports a claim when the claim's normalised key appears in
    the source text. Returns the claims with a ``sources`` list of
    ``{"domain", "url"}`` records (deduplicated by domain).
    """
    prepared = [
        (_normalise(_source_text(s)), _source_domain(s), str(s.get("url") or "")) for s in sources
    ]
    bound: list[dict[str, Any]] = []
    for claim in claims:
        key = claim["key"]
        matched: list[dict[str, str]] = []
        seen_domains: set[str] = set()
        for text, domain, url in prepared:
            if key and key in text:
                dedup_key = domain or url
                if dedup_key in seen_domains:
                    continue
                seen_domains.add(dedup_key)
                matched.append({"domain": domain, "url": url})
        bound.append({**claim, "sources": matched})
    return bound


def corroborate(claim: dict[str, Any]) -> dict[str, Any]:
    """Step 3 — count independent domains supporting one bound claim."""
    domains = {s["domain"] for s in claim.get("sources", []) if s.get("domain")}
    independent = len(domains)
    return {
        **claim,
        "independent_sources": independent,
        "corroborated": independent >= MIN_INDEPENDENT_SOURCES,
    }


def verdict(claim: dict[str, Any]) -> dict[str, Any]:
    """Step 4 — per-claim verdict: ``pass`` when corroborated, else ``flag``."""
    return {
        **claim,
        "verdict": "pass" if claim.get("corroborated") else "flag",
    }


def check_draft(draft: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Run all four steps and return the draft-level fact-check report.

    ``sources`` is a list of dicts with at least ``url``/``source`` and
    ``title``/``content``/``snippet`` (the shape ``sources_registry``
    produces). The draft gate is ``pass`` only when every claim passes;
    a draft with no verifiable claims is ``flag`` (nothing to check).
    """
    claims = extract_claims(draft)
    bound = bind_claims(claims, sources)
    checked = [verdict(corroborate(c)) for c in bound]
    passed = sum(1 for c in checked if c["verdict"] == "pass")
    flagged = len(checked) - passed
    if not checked:
        gate = "flag"
    elif flagged == 0:
        gate = "pass"
    else:
        gate = "flag"
    return {
        "verdict": gate,
        "claim_count": len(checked),
        "passed": passed,
        "flagged": flagged,
        "min_independent_sources": MIN_INDEPENDENT_SOURCES,
        "claims": checked,
    }


__all__ = [
    "MIN_INDEPENDENT_SOURCES",
    "bind_claims",
    "check_draft",
    "corroborate",
    "extract_claims",
    "verdict",
]
