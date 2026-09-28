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
