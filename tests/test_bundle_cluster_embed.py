"""Bundle/cluster behavioral-embedding contracts (rugbrain.bundle_cluster).

Pure-function coverage for the detection fingerprint embedders:
* embed_bundle_profile — 128-dim behavioral vector: score clamping,
  scale normalization, behavior/risk one-hots, chain one-hot, deterministic
  address + structural hashes
* embed_cluster_profile — 192-dim cluster vector: size/density/activity
  encodings, sleeper flag

These run with NO Qdrant/network — the module imports heavy clients
lazily inside the async functions, which we do not exercise here.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from rugbrain.bundle_cluster import (
    embed_bundle_profile,
    embed_cluster_profile,
)


def _full_bundle() -> dict[str, Any]:
    return {
        "confidence": 1.5,  # must clamp to 1.0
        "atomic_block_score": 0.8,
        "common_funder_score": 0.6,
        "temporal_score": 0.4,
        "distribution_anomaly_score": 0.2,
        "concentration_score": 0.9,
        "wallets_in_earliest_block": 250,  # clamps to 1.0 at /100
        "total_bundle_wallets": 50,
        "unique_funders": 1,
        "common_funder_address": "0xfunder",
        "token_address": "0xtoken",
        "bundle_wallets": [f"0xw{i}" for i in range(10)],
        "top3_holder_percent": 55.0,
        "top10_holder_percent": 80.0,
        "block_span": 2,
        "earliest_block": 100,
        "launch_block": 100,  # block-0 bundle flag
        "behaviors": ["coordinated_buy", "same_amount", "sybil_pattern"],
        "chain": "Base",  # case-insensitive substring match
        "risk_tags": ["scam", "sybil"],
        "classification": "wash_trade ring",
        "avg_buy_amount": 5000,
        "sell_ratio": 0.5,
    }


def test_bundle_profile_shape_and_empty_defaults():
    empty = embed_bundle_profile({})
    assert len(empty) == 128
    assert all(v == 0.0 for v in np.asarray(empty)[16:32])  # no behaviors


def test_bundle_profile_score_clamping_and_scale_signals():
    vec = np.asarray(embed_bundle_profile(_full_bundle()))
    assert vec[0] == 1.0  # confidence 1.5 clamped
    assert vec[1] == pytest_approx(0.8)
    assert vec[6] == 1.0  # 250 wallets / 100 clamped
    assert vec[7] == pytest_approx(0.5)  # 50 / 100
    assert vec[8] == pytest_approx(1.0)  # 1 funder -> 1/max(1,1)
    assert vec[9] == 1.0  # common funder present
    assert vec[10] == pytest_approx(0.55)  # top3 pct
    assert vec[11] == pytest_approx(0.80)  # top10 pct
    assert vec[12] == pytest_approx(0.5)  # 1/span(2)
    assert vec[13] == 1.0  # earliest == launch block


def pytest_approx(x: float) -> float:
    return float(np.float32(x))


def test_bundle_profile_behavior_one_hots():
    vec = np.asarray(embed_bundle_profile(_full_bundle()))
    assert vec[16] == 1.0  # coordinated_buy -> slot 0
    assert vec[18] == 1.0  # same_amount -> slot 2
    assert vec[31] == 1.0  # sybil_pattern -> slot 15
    assert vec[17] == 0.0  # staggered_entry absent


def test_bundle_profile_chain_one_hot_case_insensitive():
    vec = np.asarray(embed_bundle_profile(_full_bundle()))
    assert vec[50] == 1.0  # "base" matched inside "Base" (slot 2)
    assert vec[48] == 0.0  # solana not set
    assert vec[49] == 0.0  # ethereum not set


def test_bundle_profile_risk_tags_and_classification_text():
    vec = np.asarray(embed_bundle_profile(_full_bundle()))
    assert vec[64] == 1.0  # scam tag
    assert vec[70] == 1.0  # sybil tag
    assert vec[67] == 1.0  # wash_trade via classification TEXT match
    assert vec[65] == 0.0  # rug absent


def test_bundle_profile_address_hash_deterministic_but_input_sensitive():
    a = np.asarray(embed_bundle_profile(_full_bundle()))
    b = np.asarray(embed_bundle_profile({**_full_bundle(), "token_address": "0xother"}))
    addr_slice_a = a[32:48]
    addr_slice_b = b[32:48]
    assert not np.allclose(addr_slice_a, addr_slice_b)  # input changed -> hash moved
    c = np.asarray(embed_bundle_profile(_full_bundle()))
    assert np.allclose(addr_slice_a, c[32:48])  # same input -> same hash


def test_bundle_profile_structural_hash_covers_full_payload():
    a = np.asarray(embed_bundle_profile(_full_bundle()))
    b = np.asarray(embed_bundle_profile({**_full_bundle(), "avg_buy_amount": 9999}))
    assert not np.allclose(a[86:128], b[86:128])


def test_cluster_profile_shape_and_size_encodings():
    empty = embed_cluster_profile({})
    assert len(empty) == 192
    # size defaults to 1: log1p(1)/10
    assert empty[0] == pytest_approx(np.log1p(1) / 10.0)

    big = embed_cluster_profile({"size": 1500, "density": 0.7})
    vbig = np.asarray(big)
    assert vbig[1] == 1.0  # 1500/1000 clamped
    assert vbig[2] == pytest_approx(0.7)


def test_cluster_profile_activity_and_sleeper_flag():
    v = np.asarray(
        embed_cluster_profile(
            {
                "age_days": 365,
                "total_transactions": 0,  # log1p(0)=0
                "burst_score": 2.0,  # clamped
                "sleeper_cluster": True,
            }
        )
    )
    assert v[10] == pytest_approx(1.0)  # 365/365
    assert v[11] == 0.0
    assert v[14] == 1.0  # burst clamped
    assert v[16] == 1.0  # sleeper flag
