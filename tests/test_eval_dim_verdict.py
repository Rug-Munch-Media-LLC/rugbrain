"""Unit tests for rugbrain.eval.dim_verdict — the dimension decision table.

Split out of `eval/dim_preflight.py` on 2026-09-29 (size ratchet). The table is
where a dimension split is actually decided, so each branch is pinned here by
name: an unreadable store width is never a pass, a declared narrow pin is never
a false split, and an undeclared collection is reported rather than waved
through.
"""

from __future__ import annotations

from rugbrain.eval.dim_verdict import DimVerdict, classify


def _c(**kw: object) -> tuple[bool, str]:
    """Call classify with test defaults, unpacked as (ok, detail)."""
    args: dict[str, object] = {
        "collection": "defi_exploits_8b",
        "probe_dim": 4096,
        "backend": "qwen3-embedding-8b",
        "store_dim": 4096,
        "source": "config.params.vectors.size (unnamed)",
        "declared": 4096,
    }
    args.update(kw)
    return tuple(classify(**args))  # type: ignore[arg-type]


class TestUnreadableStoreIsNeverAPass:
    def test_store_width_zero_fails_even_when_probe_and_declaration_agree(self) -> None:
        ok, detail = _c(store_dim=0)
        assert ok is False
        assert "could not read store width" in detail

    def test_detail_names_the_source_that_failed(self) -> None:
        _, detail = _c(store_dim=0, source="GET http://x/collections/y -> HTTP 503")
        assert "HTTP 503" in detail


class TestPinnedByDesign:
    def test_declared_narrow_pin_passes_despite_global_probe_mismatch(self) -> None:
        # The core regression: a 2560-pinned collection with a 4096 embedder is
        # the DESIGN, not a split. Flagging it is the cry-wolf failure.
        ok, detail = _c(collection="rug_events", store_dim=2560, declared=2560, probe_dim=4096)
        assert ok is True
        assert "pinned by design" in detail

    def test_pin_detail_shows_both_widths(self) -> None:
        _, detail = _c(collection="rug_events", store_dim=2560, declared=2560)
        assert "declared 2560 == store 2560" in detail
        assert "probe is 4096" in detail


class TestRealSplits:
    def test_declaration_disagrees_with_store(self) -> None:
        ok, detail = _c(store_dim=1536, declared=4096)
        assert ok is False
        assert "DIMENSION SPLIT" in detail
        assert "declares 4096" in detail
        assert "1536-wide" in detail

    def test_embedder_disagreement_against_a_matching_declaration_is_a_pin(self) -> None:
        # Branch precedence: the "pinned by design" test runs BEFORE the
        # probe/store comparison, so declared == store with a different global
        # probe is reported as a deliberate pin, not as a split.
        #
        # [REVIEW] This also means the later `probe_dim != store_dim` branch is
        # unreachable while a declaration is present: reaching it needs
        # declared == store_dim and declared != probe_dim to both be false,
        # which implies probe_dim == store_dim. Preserved verbatim from the
        # pre-split table rather than "fixed" -- changing it could move a gate
        # floor, and a floor may only move to a MEASURED value.
        ok, detail = _c(collection="x", store_dim=2560, declared=2560, probe_dim=4096)
        assert ok is True
        assert "pinned by design" in detail

    def test_declaration_matches_store_but_is_neither_probe_nor_store_width(self) -> None:
        # declared == store == 1536 with a 4096 probe: the pin branch fires, so
        # this is a deliberate pin even though 1536 is not a width this repo
        # documents. See the branch-precedence note above.
        ok, detail = _c(store_dim=1536, declared=1536, probe_dim=4096)
        assert ok is True
        assert "pinned by design" in detail


class TestUndeclaredCollection:
    def test_matching_probe_and_store_passes(self) -> None:
        ok, _ = _c(declared=None, probe_dim=4096, store_dim=4096)
        assert ok is True

    def test_mismatched_probe_and_store_fails(self) -> None:
        ok, detail = _c(declared=None, probe_dim=4096, store_dim=2560)
        assert ok is False
        assert "unclassified collection" in detail

    def test_unreadable_store_still_fails_before_reaching_this_branch(self) -> None:
        ok, detail = _c(declared=None, store_dim=0)
        assert ok is False
        assert "could not read store width" in detail
        assert "unclassified" not in detail


class TestAllAgree:
    def test_pass_detail_names_all_three(self) -> None:
        ok, detail = _c()
        assert ok is True
        assert "probe 4096 == store 4096 == declared" in detail
        assert "qwen3-embedding-8b" in detail


class TestDimVerdictType:
    def test_unpacks_as_ok_detail(self) -> None:
        ok, detail = DimVerdict(True, "fine")
        assert (ok, detail) == (True, "fine")

    def test_repr_shows_both_fields(self) -> None:
        assert "ok=False" in repr(DimVerdict(False, "bad"))
        assert "bad" in repr(DimVerdict(False, "bad"))
