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
class TestMismatchFlagging:
    def test_declared_4096_with_live_2560_is_flagged(self):
        m = DimMismatch("market_data", 4096, 2560, "synthetic")
        assert not m.is_explainable_by_retired

    def test_live_retired_width_is_explainable(self):
        """A base whose live width is a declared retired lane is retention."""
        base = next(d for d in DECLARED if d.collection == "threat_intel_v2")
        m = DimMismatch("threat_intel_v2", base.dim, base.retired_dims[0], "synthetic")
        assert m.is_explainable_by_retired

    def test_rug_events_is_the_one_that_matters(self):
        """Largest corpus with no twin, so a canonical write is refused forever.

        MEASURED: live width is 2560 and the resolver finds no ``_8b`` twin, so
        the 4096 write lane is stranded against a 2560 collection. It is
        deliberately NOT in ``retired_dims``: there is no twin holding the 4096
        points, so this is a real defect, not the documented no-overwrite split.
        """
        d = declared_corpus("rug_events")
        assert d is not None
        assert d.dim == CANONICAL_LANE_DIM
        assert d.observed_live_dim == PRESERVED_LANE_DIM
        assert d.retired_dims == ()

    def test_stranded_lane_is_not_retirement(self):
        """Retention needs a twin; without one, a width gap is a finding.

        ``resolve_collection`` returns a name UNCHANGED when it has no ``_8b``
        twin, so the twin test is "did it change", not "is it truthy". A 2560
        name whose declaration writes at 4096 and which owns no twin is stranded:
        nothing holds the canonical points, so it must not be masked.
        """
        stranded = [
            d
            for d in DECLARED
            if d.observed_live_dim not in (None, d.dim)
            and lr.resolve_collection(d.collection) == d.collection
        ]
        assert "rug_events" in {d.collection for d in stranded}
        for d in stranded:
            assert d.observed_live_dim not in d.retired_dims, d.corpus_key


@pytest.mark.asyncio
class TestLiveVerification:
    async def test_matching_widths_report_clean(self):
        async with client_for({"threat_intel_v2_8b": CANONICAL_LANE_DIM}) as client:
            report = await verify_live_widths(client)
        assert report.mismatches == []
        assert report.undeclared_collections == []
        assert report.checked == 1

    async def test_live_width_disagreeing_with_declared_is_flagged(self):
        """A collection whose name says canonical but whose width is not."""
        base = next(d for d in DECLARED if d.collection == "market_data")
        wrong = CANONICAL_LANE_DIM if base.dim != CANONICAL_LANE_DIM else PRESERVED_LANE_DIM
        async with client_for({"market_data": wrong}) as client:
            report = await verify_live_widths(client)
        assert [m.collection for m in report.mismatches] == ["market_data"]
        assert report.mismatches[0].live_dim == wrong

    async def test_retired_lane_live_width_is_not_a_mismatch(self):
        """A width this corpus genuinely lived at is retention, not a defect.

        ``threat_intel_v2`` is measured at 2560 while its declaration writes at
        4096, and 2560 IS in its ``retired_dims`` because the ``_8b`` twin holds
        the 4096 points — the documented no-overwrite split.
        """
        base = next(d for d in DECLARED if d.collection == "threat_intel_v2")
        assert PRESERVED_LANE_DIM in base.retired_dims
        async with client_for({"threat_intel_v2": base.retired_dims[0]}) as client:
            report = await verify_live_widths(client)
        assert report.mismatches == []
        assert report.matched == 1

    async def test_stranded_width_with_no_twin_is_flagged(self):
        """The counterpart: no twin, so nothing holds the canonical points.

        ``rug_events`` is 2560 with no ``_8b`` twin, so the 4096 write lane has
        nowhere to go. That is a finding, and it must survive verification.
        """
        async with client_for({"rug_events": PRESERVED_LANE_DIM}) as client:
            report = await verify_live_widths(client)
        assert [m.collection for m in report.mismatches] == ["rug_events"]

    async def test_undeclared_live_collection_is_reported(self):
        """Live in Qdrant, absent from the manifest — the gap the operator wants."""
        async with client_for({"some_corpus_nobody_declared": CANONICAL_LANE_DIM}) as client:
            report = await verify_live_widths(client)
        assert report.undeclared_collections == ["some_corpus_nobody_declared"]
        assert not report.is_clean

    async def test_twin_is_not_reported_undeclared(self):
        """A live ``_8b`` twin resolves to its base's declaration."""
        async with client_for({"defi_exploits_8b": CANONICAL_LANE_DIM}) as client:
            report = await verify_live_widths(client)
        assert report.undeclared_collections == []
        assert report.checked == 1

    async def test_unreachable_is_a_gap_not_a_mismatch(self):
        """A Qdrant that cannot be reached must never read as a dim defect."""
        async with client_for({"defi_exploits_8b": None}) as client:
            report = await verify_live_widths(client)
        assert report.mismatches == []
        assert report.unreachable == ["defi_exploits_8b"]


# --------------------------------------------------------------------------
# 4. BASE/_8b IS EXPRESSED VIA THE EXISTING RESOLVER
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
