"""Tests for the split-out 8B canary modules.

``test_canary_routing.py`` pins the behaviour through the facade. These tests
pin the extracted modules directly, so a future edit that quietly re-merges the
credential path into the config gate — or drops the dimension check from the
provider module — fails here rather than in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rugbrain.canary_config as config
import rugbrain.canary_embedding as embedding
import rugbrain.canary_routing as facade
import rugbrain.canary_sanitize as sanitize

# ── facade surface is stable ────────────────────────────────────────────


def test_facade_reexports_every_public_name() -> None:
    for name in facade.__all__:
        assert hasattr(facade, name), f"facade lost {name}"


def test_facade_bindings_are_the_module_objects() -> None:
    # Callers resolve through the facade; a copy-paste redefinition would make
    # the facade and the real module disagree.
    assert facade.canary_target_for is config.canary_target_for
    assert facade.validate_canary_vector is embedding.validate_canary_vector
    assert facade.sanitize_canary_result is sanitize.sanitize_canary_result
    assert facade.CanaryConfigError is config.CanaryConfigError


# ── config gate ─────────────────────────────────────────────────────────


def test_config_collections_are_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_8B_CANARY_ENABLED", raising=False)
    monkeypatch.setenv("RAG_8B_CANARY_COLLECTIONS", "known_scams")
    assert config.canary_collections() == frozenset()
    assert config.canary_enabled() is False


def test_config_rejects_an_unallowlisted_configured_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_8B_CANARY_ENABLED", "1")
    monkeypatch.setenv("RAG_8B_CANARY_COLLECTIONS", "known_scams,not_a_public_collection")
    with pytest.raises(config.CanaryConfigError):
        config.canary_collections()


def test_config_target_is_none_for_a_configured_but_unchecked_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_8B_CANARY_ENABLED", "true")
    monkeypatch.setenv("RAG_8B_CANARY_COLLECTIONS", "known_scams")
    assert config.canary_target_for("defi_exploits") is None
    assert config.canary_target_for("known_scams") == "known_scams_8b"


def test_config_target_requires_an_explicit_collection_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_8B_CANARY_ENABLED", "1")
    monkeypatch.delenv("RAG_8B_CANARY_COLLECTIONS", raising=False)
    with pytest.raises(config.CanaryConfigError):
        config.canary_target_for("known_scams")


# ── vector validation ───────────────────────────────────────────────────


def test_validate_rejects_the_wrong_dimension() -> None:
    with pytest.raises(config.CanaryConfigError):
        embedding.validate_canary_vector([0.0] * (config.CANARY_DIM - 1))


def test_validate_rejects_non_finite_values() -> None:
    vector = [0.0] * config.CANARY_DIM
    vector[7] = float("nan")
    with pytest.raises(config.CanaryConfigError):
        embedding.validate_canary_vector(vector)
    vector[7] = float("inf")
    with pytest.raises(config.CanaryConfigError):
        embedding.validate_canary_vector(vector)


def test_validate_returns_a_copied_float_list() -> None:
    vector = [1] * config.CANARY_DIM
    out = embedding.validate_canary_vector(vector)
    assert isinstance(out, list)
    assert all(isinstance(v, float) for v in out)
    out[0] = 99.0
    assert vector[0] == 1


# ── credential resolution ───────────────────────────────────────────────


def test_api_key_prefers_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    keyfile = tmp_path / "key"
    keyfile.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "from-env")
    monkeypatch.setenv("DEEPINFRA_KEY_FILE", str(keyfile))
    assert embedding._api_key() == "from-env"


def test_api_key_falls_back_to_the_configured_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keyfile = tmp_path / "key"
    keyfile.write_text("from-file\n", encoding="utf-8")
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.setenv("DEEPINFRA_KEY_FILE", str(keyfile))
    assert embedding._api_key() == "from-file"


def test_api_key_unavailable_raises_rather_than_returning_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Point the fallback at an empty dir so neither the env var nor any
    # well-known key path can resolve: it must raise, not return None.
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    monkeypatch.setenv("DEEPINFRA_KEY_FILE", str(tmp_path / "absent"))
    monkeypatch.setattr(embedding, "KEY_FILE_PATHS", (str(tmp_path / "absent"),))
    with pytest.raises(config.CanaryUnavailable):
        embedding._api_key()


# ── sanitiser ───────────────────────────────────────────────────────────


def test_sanitizer_relabels_nested_containers_and_strings() -> None:
    hit = {
        "collection": "known_scams_8b",
        "meta": [{"target_collection": "known_scams_8b"}],
        "content": "sourced from known_scams_8b upstream",
    }
    out = sanitize.sanitize_canary_result(hit, "known_scams")
    assert out["collection"] == "known_scams"
    assert out["meta"][0]["target_collection"] == "known_scams"
    assert out["content"] == "sourced from known_scams upstream"


def test_sanitizer_preserves_tuples_and_scalars() -> None:
    out = sanitize.sanitize_canary_result(("known_scams_8b", 5, None), "known_scams")
    assert out == ("known_scams", 5, None)
