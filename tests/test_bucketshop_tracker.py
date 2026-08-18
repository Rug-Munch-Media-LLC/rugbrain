"""Tests for rugbrain.monitors.bucketshop_tracker."""

from __future__ import annotations

from rugbrain.monitors.bucketshop_tracker import (
    BUCKETSHOP_EXCHANGES,
    get_all_bucketshops,
    get_bucketshop,
    get_scam_signals,
)


class TestBucketshopTracker:
    def test_bucketshop_exchanges_defined(self):
        assert len(BUCKETSHOP_EXCHANGES) >= 20
        assert "FTX" in BUCKETSHOP_EXCHANGES
        assert "Mt. Gox" in BUCKETSHOP_EXCHANGES
        assert "Terra" in "\t".join(BUCKETSHOP_EXCHANGES) or "LUNA" in "\t".join(
            BUCKETSHOP_EXCHANGES
        )

    def test_get_all_bucketshops_returns_dict(self):
        result = get_all_bucketshops()
        assert isinstance(result, dict)

    def test_get_bucketshop_known(self):
        result = get_bucketshop("FTX")
        # May return None if cycle hasn't run — check it doesn't crash
        if result is not None:
            assert isinstance(result, dict)

    def test_get_bucketshop_unknown(self):
        # Should handle gracefully without crash
        result = get_bucketshop("NonExistentExchange123")
        if result is not None:
            assert isinstance(result, dict)

    def test_get_scam_signals_returns_list(self):
        signals = get_scam_signals()
        assert isinstance(signals, list)
