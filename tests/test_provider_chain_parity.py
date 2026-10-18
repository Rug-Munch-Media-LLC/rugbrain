"""Provider-chain parity with ContextCrypto (operator directive 2026-10-12).

RugBrain and ContextCrypto must expose the SAME hosted 8B lane shape:
DeepInfra primary, SiliconFlow backup, OpenRouter backup, OpenRouter
second-account backup — all serving Qwen3-Embedding-8B / Qwen3-Reranker-8B.

These are STRUCTURE tests (no network): they pin the order, the 8B model
ids, and the credential resolution (env first, then the spooled key file)
so a rung can never silently drop out of the chain.
"""

from __future__ import annotations

import pytest

from rugbrain import embedder, rerank_lanes

EXPECTED = ("siliconflow", "openrouter", "openrouter2", "deepinfra")
EXPECTED_KEY_FILES = {
    "deepinfra": "/srv/secrets/deepinfra_api_key",
    "siliconflow": "/srv/secrets/siliconflow_api_key",
    "openrouter": "/srv/secrets/openrouter_account2_api_key",
    "openrouter2": "/srv/secrets/openrouter_account2_api_key",
}


@pytest.mark.parametrize("module", [embedder, rerank_lanes])
def test_chain_order_matches_contextcrypto(module) -> None:
    assert module.DEFAULT_ORDER == EXPECTED


@pytest.mark.parametrize("module", [embedder, rerank_lanes])
def test_every_rung_is_registered(module) -> None:
    assert set(EXPECTED) <= set(module._PROVIDERS)


@pytest.mark.parametrize("module", [embedder, rerank_lanes])
def test_every_rung_serves_the_8b_model(module) -> None:
    for name in EXPECTED:
        _, model, _, _ = module._PROVIDERS[name]
        assert "8B" in model or "8b" in model, f"{name} is not on the 8B model: {model}"


@pytest.mark.parametrize("module", [embedder, rerank_lanes])
def test_credential_files_are_the_spooled_keys(module) -> None:
    for name, path in EXPECTED_KEY_FILES.items():
        _, _, _, key_file = module._PROVIDERS[name]
        assert key_file == path, f"{name} reads {key_file}, expected {path}"


@pytest.mark.parametrize("module", [embedder, rerank_lanes])
def test_key_resolution_prefers_env_then_file(module, tmp_path, monkeypatch) -> None:
    keyfile = tmp_path / "key"
    keyfile.write_text("from-file\n", encoding="utf-8")
    _, _, env_name, _ = module._PROVIDERS["deepinfra"]
    monkeypatch.setenv(env_name, "from-env")
    module._KEYS.clear()
    assert module._key("deepinfra") == "from-env"
    monkeypatch.delenv(env_name, raising=False)
    module._KEYS["deepinfra"] = ""
    monkeypatch.setattr(
        module, "_PROVIDERS", dict(module._PROVIDERS, deepinfra=("u", "m", env_name, str(keyfile)))
    )
    module._KEYS.clear()
    assert module._key("deepinfra") == "from-file"
    module._KEYS.clear()


@pytest.mark.parametrize("module", [embedder, rerank_lanes])
def test_effective_order_is_canonical_without_override(module, monkeypatch) -> None:
    """The EFFECTIVE chain (what dispatch walks) must be the canonical order.

    ``DEFAULT_ORDER`` alone is not enough: the live drift of 2026-10-16 was an
    ``RERANK_PROVIDER_ORDER=siliconflow,openrouter,deepinfra`` override in
    ``/srv/secrets/rugbrain.env`` that demoted DeepInfra to third while
    ContextCrypto's ``paid-first`` chain kept it primary. Pinning the default
    left the override free to reorder the chain, so parity broke silently.
    """
    monkeypatch.delenv("RERANK_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("EMBED_PROVIDER_ORDER", raising=False)
    assert module._order() == EXPECTED


@pytest.mark.parametrize("module", [embedder, rerank_lanes])
def test_override_cannot_drop_a_rung(module, monkeypatch) -> None:
    """An override may reorder, but every canonical rung must still be present.

    A partial override (e.g. ``deepinfra``) must not silently shrink the chain
    to one provider — the tail is always appended from ``DEFAULT_ORDER``.
    """
    monkeypatch.setenv("RERANK_PROVIDER_ORDER", "deepinfra")
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "deepinfra")
    order = module._order()
    assert order[0] == "deepinfra"
    assert set(EXPECTED) <= set(order)
