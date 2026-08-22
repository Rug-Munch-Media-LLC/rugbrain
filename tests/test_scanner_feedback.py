"""Tests for rugbrain.scanner_feedback — scanner-to-RAG feedback loop."""

from __future__ import annotations

import importlib as _il

scanner_feedback = _il.import_module("rugbrain.scanner_feedback")

mod = scanner_feedback


def test_feedback_collection_default() -> None:
    """FEEDBACK_COLLECTION is a string."""
    assert isinstance(mod.FEEDBACK_COLLECTION, str)
    assert len(mod.FEEDBACK_COLLECTION) > 0


def test_router_exists() -> None:
    """Router is a FastAPI APIRouter."""
    from fastapi import APIRouter

    assert isinstance(mod.scanner_feedback_router, APIRouter)
