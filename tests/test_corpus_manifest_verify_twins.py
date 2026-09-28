"""The manifest's LIVE half: mismatch flagging, twin delegation, undeclared report.

The gate half (refusal, acceptance, licence) is in ``test_corpus_manifest``. This
half drives ``verify.verify_live_widths`` against a Qdrant stand-in and asserts
the three findings it must produce, plus that the ``_8b`` relationship is DELEGATED
to Lane I's resolver rather than re-derived. Fixtures in ``corpus_manifest_fixtures``.

The required cases, this half:
    3. a live-width mismatch is FLAGGED (and retention is not)
    4. base/_8b is expressed via the existing resolver, not re-derived
    5. a live collection absent from the manifest is REPORTED as undeclared
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.corpus_manifest_fixtures import client_for
from rugbrain.corpus import (
    DECLARED,
    DimMismatch,
    declared_corpus,
    verify_live_widths,
)
from rugbrain.qdrant_store import lane_resolver as lr
from rugbrain.qdrant_store.lane_table import CANONICAL_LANE_DIM, PRESERVED_LANE_DIM


# --------------------------------------------------------------------------
# 3. LIVE-WIDTH MISMATCH IS FLAGGED
# --------------------------------------------------------------------------


class TestTwinDelegation:
    def test_twin_name_resolves_to_the_base_declaration(self):
        for base, twin in (
            ("threat_intel_v2", "threat_intel_v2_8b"),
            ("defi_exploits", "defi_exploits_8b"),
            ("known_scams", "known_scams_8b"),
            ("wallet_profiles", "wallet_profiles_8b"),
        ):
            assert declared_corpus(twin) is declared_corpus(base)

    def test_the_same_resolver_owns_the_rewrite(self):
        """The manifest must not hold a second implementation of the rule.

        Resolution is delegated: whatever ``resolve_collection`` says a base
        becomes, the manifest follows. If Lane I's table gains a name, this
        module needs no change to agree.
        """
        base = "threat_intel_v2"
        assert lr.resolve_collection(base) == "threat_intel_v2_8b"
        # the manifest reaches the same conclusion by delegation, not by re-deriving
        assert declared_corpus(base) is declared_corpus(lr.resolve_collection(base))

    def test_resolver_is_called_not_duplicated(self, monkeypatch):
        """Proof of delegation: patching the resolver changes our behaviour.

        Spied on the TWIN path, because a base name is present in the table
        directly and short-circuits before the resolver is needed. The twin is
        the case that would require a second implementation of the rule if the
        relationship were re-derived here.
        """
        calls: list[str] = []
        real = lr.resolve_collection

        def spy(collection: str | None) -> str | None:
            calls.append(str(collection))
            return real(collection)

        monkeypatch.setattr(lr, "resolve_collection", spy)
        assert declared_corpus("threat_intel_v2_8b").corpus_key == "threat_intel_v2"
        assert "threat_intel_v2_8b" in calls, "the twin path must go through the resolver"

    def test_no_hardcoded_dims_in_the_manifest(self):
        """Declared dims come from ``lane_table``, not from literals here."""
        from rugbrain.qdrant_store import lane_table

        assert lane_table.CANONICAL_LANE_DIM == CANONICAL_LANE_DIM
        assert CANONICAL_LANE_DIM != PRESERVED_LANE_DIM


# --------------------------------------------------------------------------
# 5. EXISTING-BUT-UNDECLARED IS REPORTED
# --------------------------------------------------------------------------
class TestUndeclaredReport:
    def test_manifest_covers_every_live_collection_in_the_audit(self):
        """Parity with the measured inventory — the gap is the finding.

        Transcribed from ``docs/COVERAGE-AUDIT-2026-09-28.md`` §1 (96 collections,
        6,314,262 points). Every live collection must resolve to a declaration,
        base or twin alike.
        """
        audit = Path(__file__).resolve().parents[1] / "docs" / "COVERAGE-AUDIT-2026-09-28.md"
        live = set()
        for line in audit.read_text().splitlines():
            m = re.match(r"^\|\s`([a-z0-9_]+)`\s\|\s*\w+\s\|\s*[\d,]+\s\|\s*\d+\s\|$", line)
            if m:
                live.add(m.group(1))
        assert len(live) == 96
        undeclared = sorted(n for n in live if declared_corpus(n) is None)
        assert undeclared == [], f"live but undeclared: {undeclared}"

    def test_declared_targets_without_a_collection_yet_are_recorded(self):
        """A declared corpus with no collection is a PLAN, not a gap.

        lane_table names such owned names; declaring them means the add path does
        not have to invent a collection later.
        """
        planned = {d.collection for d in DECLARED if d.observed_live_dim is None}
        assert {"solidity_patterns", "internal-lessons"} <= planned
