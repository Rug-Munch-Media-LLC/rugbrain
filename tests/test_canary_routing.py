"""Default-off 8B canary routing tests."""

from __future__ import annotations

import pytest

from rugbrain.canary_routing import (
    ALLOWED_CANARY_SOURCES,
    CanaryConfigError,
    canary_target_for,
    sanitize_canary_result,
    validate_canary_vector,
)
from rugbrain.knowledge_boundary import PUBLIC_COLLECTIONS


def test_canary_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_8B_CANARY_ENABLED", raising=False)
    monkeypatch.delenv("RAG_8B_CANARY_COLLECTIONS", raising=False)

    assert canary_target_for("contract_security") is None


def test_canary_maps_only_explicit_allowlisted_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_8B_CANARY_ENABLED", "1")
    monkeypatch.setenv("RAG_8B_CANARY_COLLECTIONS", "contract_security,defi_exploits")

    assert canary_target_for("contract_security") == "contract_security_8b"
    assert canary_target_for("defi_exploits") == "defi_exploits_8b"
    assert canary_target_for("known_scams") is None
    assert "contract_security" in ALLOWED_CANARY_SOURCES
    assert ALLOWED_CANARY_SOURCES <= PUBLIC_COLLECTIONS


@pytest.mark.parametrize(
    "collection",
    [
        "codebase_reference_v2",
        "rag_documents",
        "agent_vectors",
        "security_patterns",
        "exploits",
        "cross_chain",
    ],
)
def test_canary_rejects_incomplete_or_internal_collections(
    monkeypatch: pytest.MonkeyPatch, collection: str
) -> None:
    monkeypatch.setenv("RAG_8B_CANARY_ENABLED", "1")
    monkeypatch.setenv("RAG_8B_CANARY_COLLECTIONS", collection)

    with pytest.raises(CanaryConfigError):
        canary_target_for(collection)


def test_canary_vector_must_be_4096() -> None:
    validate_canary_vector([0.0] * 4096)
    with pytest.raises(CanaryConfigError):
        validate_canary_vector([0.0] * 2560)
    with pytest.raises(CanaryConfigError):
        validate_canary_vector([])


def test_canary_rejects_invalid_config_even_for_uncorrelated_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_8B_CANARY_ENABLED", "1")
    monkeypatch.setenv("RAG_8B_CANARY_COLLECTIONS", "contract_security,codebase_reference_v2")

    with pytest.raises(CanaryConfigError):
        canary_target_for("known_scams")


def test_canary_result_sanitizer_relabels_shadow_collection() -> None:
    result = {
        "content": "report from contract_security_8b",
        "metadata": {
            "collection": "contract_security_8b",
            "source_collection": "contract_security_8b",
            "model": "private-model",
        },
    }

    sanitized = sanitize_canary_result(result, "contract_security")

    assert sanitized["content"] == "report from contract_security"
    assert sanitized["metadata"]["collection"] == "contract_security"
    assert sanitized["metadata"]["source_collection"] == "contract_security"
    assert "model" not in sanitized["metadata"]
