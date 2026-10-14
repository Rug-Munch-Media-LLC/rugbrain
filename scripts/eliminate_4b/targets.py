"""WHICH collections are re-embedded, and which are deliberately not.

This is the whole revised plan in one place, and it carries NO counts — see
``plan.py`` for why a count in a table is a bug. What lives here is only the
membership decision, which is a judgement and belongs in a file a reviewer can
read in thirty seconds.

THE JOB IS ~7.9k POINTS, NOT 60,255. Two collections are excluded outright
(``EXCLUDED``) and one of them is 87% of the original number. The reasoning
that produced that is in ``embed.py``: a payload with no text field has no
recoverable embed input, and a vector built from invented filler retrieves
nothing while costing a DeepInfra call per point.
"""

from __future__ import annotations

from dataclasses import dataclass

LEGACY_DIM = 2560
CANONICAL_DIM = 4096
TWIN_SUFFIX = "_8b"


def twin_name(base: str) -> str:
    """The 4096 twin of a base collection.

    Refuses to suffix a name that already carries the suffix. Without this the
    fill loop created ``news_v2_8b_8b`` from ``news_v2_8b`` - a lane NOTHING
    resolves to, so every point written there was silently invisible. It was
    measured GROWING while both the live lane and the orphan rose in step.
    ``lane_resolver.py`` already guards this way (``endswith(EIGHT_B_SUFFIX)``);
    this file did not, and it is the one that writes.
    """
    if base.endswith(TWIN_SUFFIX):
        raise ValueError(
            f"twin_name({base!r}) would produce {base + TWIN_SUFFIX!r} - an orphan "
            f"lane no tool resolves to. The base already carries {TWIN_SUFFIX!r}."
        )
    return base + TWIN_SUFFIX


@dataclass(frozen=True)
class Target:
    """One base and the twin it must fill. Deliberately carries NO counts.

    ``create_twin`` marks the three collections with no _8b twin. Their base is
    the only copy in Qdrant, so the twin is built from scratch and then
    verified; ``shape_from`` says which collection's vector layout the new twin
    copies, so a dense+sparse twin is never accidentally created dense-only.
    """

    base: str
    planned_base_dim: int
    note: str = ""
    create_twin: bool = False
    shape_from: str | None = None


# The collections to re-embed, measured 2026-09-29 against :6334. The names
# and their PLANNED dim are the contract; the counts are re-measured at run
# time. threat_intel_v2 is a live scanner target and drifts upward
# continuously — which is precisely why no count is stored here.
TARGETS: tuple[Target, ...] = (
    Target(
        "codebase_reference_v2",
        LEGACY_DIM,
        "twin already 99.8% full; only the tail",
    ),
    # The three with no twin: 2560 base is the ONLY copy, and B2 does not back
    # up 2560 bases. Create dense-only twins (their bases are dense-only).
    Target("rug_events", LEGACY_DIM, "NO TWIN — create", create_twin=True),
    Target("education", LEGACY_DIM, ""),
    Target("security_feeds", LEGACY_DIM, ""),
    Target("threat_intel_v2", LEGACY_DIM, "live scanner target; count drifts"),
    Target("security_alerts", LEGACY_DIM, ""),
    Target("research_v2", LEGACY_DIM, ""),
    Target("security_patterns_test", LEGACY_DIM, ""),
    Target("public_crypto_patterns", LEGACY_DIM, "NO TWIN — create", create_twin=True),
    Target("scanned_tokens", LEGACY_DIM, "NO TWIN — create", create_twin=True),
)

# EXCLUDED, deliberately and permanently. No text field, so no recoverable
# embed input. Reported, never embedded. See the module docstring.
EXCLUDED: tuple[tuple[str, str], ...] = (
    ("scan_intel", "52k points; payload has no text field — going to ClickHouse"),
    ("agent_vectors", "182 points; payload has no text field — going to ClickHouse"),
)
EXCLUDED_BASES = frozenset(n for n, _ in EXCLUDED)
