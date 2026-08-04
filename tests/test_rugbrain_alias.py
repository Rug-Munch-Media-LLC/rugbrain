"""
Tests for the RugBrain branding alias package.

``import rugbrain`` must resolve to the same module object as ``import
rmi_rag``, and submodules must be importable through either name.
"""

from __future__ import annotations


def test_rugbrain_aliases_rmi_rag():
    import rmi_rag
    import rugbrain

    assert rugbrain is rmi_rag


def test_rugbrain_submodule_imports():
    import rugbrain.alerts
    import rugbrain.market_awareness

    import rmi_rag.alerts
    import rmi_rag.market_awareness

    assert rugbrain.alerts is rmi_rag.alerts
    assert rugbrain.market_awareness is rmi_rag.market_awareness


def test_rugbrain_public_api_surface():
    import rugbrain

    for name in ("get_qdrant_store", "search_multi_collection", "AlertDispatcher"):
        assert hasattr(rugbrain, name)


def test_distribution_name():
    from importlib.metadata import metadata

    meta = metadata("rugbrain")
    assert meta["Name"] == "rugbrain"
