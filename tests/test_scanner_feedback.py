"""Tests for rugbrain.scanner_feedback — scanner-to-RAG feedback loop."""

from __future__ import annotations

from rugbrain import scanner_feedback as mod


def test_feedback_collection_default():
    """FEEDBACK_COLLECTION is a string."""
    assert isinstance(mod.FEEDBACK_COLLECTION, str)
    assert len(mod.FEEDBACK_COLLECTION) > 0


def test_router_exists():
    """Router is a FastAPI APIRouter."""
    from fastapi import APIRouter

    assert isinstance(mod.scanner_feedback_router, APIRouter)
