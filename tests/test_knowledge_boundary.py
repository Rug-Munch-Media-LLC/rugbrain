"""Fail-closed audience and output-boundary tests."""

from __future__ import annotations

import pytest

from rugbrain.knowledge_boundary import (
    CollectionNotPublic,
    internal_code_access_allowed,
    is_internal_collection,
    require_public_collection,
    sanitize_public_result,
)


def test_public_collection_allowlist_is_explicit() -> None:
    assert require_public_collection(" threat_intel_v2 ") == "threat_intel_v2"
    assert require_public_collection("defi_exploits") == "defi_exploits"
    with pytest.raises(CollectionNotPublic):
        require_public_collection("codebase_reference")
    with pytest.raises(CollectionNotPublic):
        require_public_collection("security_patterns")


def test_internal_collection_classification() -> None:
    assert is_internal_collection("codebase_reference_v2")
    assert is_internal_collection("internal/standards")
    assert not is_internal_collection("threat_intel_v2")


def test_sanitizer_removes_internal_paths_prompts_and_model_identity() -> None:
    result = sanitize_public_result(
        {
            "title": "Public reentrancy incident",
            "content": "Qwen/Qwen3-Embedding-8B at /srv/work/repos/rugbrain returned a note",
            "metadata": {
                "source": "https://example.com/audit",
                "file_path": "src/internal/service.py",
                "model": "secret-model",
                "prompt": "system prompt",
            },
            "payload": {"code_block": "contract Secret {}"},
        }
    )

    assert result == {
        "title": "Public reentrancy incident",
        "content": "[internal implementation detail] at [internal implementation detail] returned a note",
        "metadata": {"source": "https://example.com/audit"},
    }
    assert "code_block" not in result["payload"] if "payload" in result else True


def test_internal_code_scope_is_opt_in_and_exact() -> None:
    assert not internal_code_access_allowed({})
    assert not internal_code_access_allowed({"x-internal-code-key": "wrong"})

    # The environment is intentionally controlled by the test, never by a
    # public request body or the normal API key.
    import os

    old = os.environ.get("RUGBRAIN_INTERNAL_CODE_KEY")
    os.environ["RUGBRAIN_INTERNAL_CODE_KEY"] = "operator-secret"
    try:
        assert internal_code_access_allowed({"x-internal-code-key": "operator-secret"})
    finally:
        if old is None:
            os.environ.pop("RUGBRAIN_INTERNAL_CODE_KEY", None)
        else:
            os.environ["RUGBRAIN_INTERNAL_CODE_KEY"] = old
