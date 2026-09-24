"""Contract tests for the core embedding routes."""

from __future__ import annotations

import pytest
import respx

from rugbrain.main import app
from rugbrain.routes.core import EmbedRequest, embed_text


def test_embed_route_exposes_canonical_and_compatibility_paths() -> None:
    """The canonical API route and the legacy root alias are both mounted."""
    paths = set(app.openapi()["paths"])
    assert "/api/v1/embed" in paths
    assert "/embed" in paths


@pytest.mark.asyncio
async def test_embed_normalizes_provider_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider configured at /v1 must call /v1/embeddings exactly once."""
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
    monkeypatch.setenv("DEEPINFRA_EMBED_URL", "https://provider.test/v1")
    monkeypatch.setenv("QWEN_EMBED_URL", "http://local.test/v1")
    monkeypatch.setenv("DEEPINFRA_EMBED_MODEL", "test-embed-model")

    with respx.mock(assert_all_called=False) as mock:
        provider_route = mock.post("https://provider.test/v1/embeddings").respond(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}]},
        )
        result = await embed_text(EmbedRequest(text="health smoke"))

    assert "served_by" not in result
    assert "model" not in result
    assert result["embedding"] == [0.1, 0.2]
    assert provider_route.called
