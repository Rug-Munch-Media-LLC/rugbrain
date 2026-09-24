"""Contract tests for the core embedding routes."""

from __future__ import annotations

from rugbrain.main import app


def test_embed_route_exposes_canonical_and_compatibility_paths() -> None:
    """The canonical API route and the legacy root alias are both mounted."""
    paths = set(app.openapi()["paths"])
    assert "/api/v1/embed" in paths
    assert "/embed" in paths
