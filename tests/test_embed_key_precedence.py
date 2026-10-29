"""A stale environment export must never shadow the secrets file.

Measured 2026-10-25: the fleet secrets-file key authenticated (200, dim 4096)
while ``SILICONFLOW_API_KEY`` exported in five env files returned 401 code
30014 — and the env won because it was resolved first.
"""

from __future__ import annotations

import pytest

from rugbrain import embedder_providers

pytestmark = pytest.mark.integration
from rugbrain.embedder_providers import hosted, local


def test_secrets_file_beats_stale_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    key_file = tmp_path / "siliconflow_api_key"  # type: ignore[operator]
    key_file.write_text("file-good-key\n", encoding="utf-8")
    monkeypatch.setitem(
        hosted._PROVIDERS,
        "siliconflow",
        ("https://example.invalid/v1/embeddings", "m", "SILICONFLOW_API_KEY", str(key_file)),
    )
    monkeypatch.setenv("SILICONFLOW_API_KEY", "env-stale-key")
    hosted._KEYS.clear()
    assert hosted._key("siliconflow") == "file-good-key"


def test_env_is_used_only_when_file_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    missing = tmp_path / "absent"  # type: ignore[operator]
    monkeypatch.setitem(
        hosted._PROVIDERS,
        "deepinfra",
        ("https://example.invalid/v1/embeddings", "m", "DEEPINFRA_API_KEY", str(missing)),
    )
    monkeypatch.setenv("DEEPINFRA_API_KEY", "env-fallback-key")
    hosted._KEYS.clear()
    assert hosted._key("deepinfra") == "env-fallback-key"


def test_local_lane_file_beats_stale_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    secrets_file = tmp_path / "embed-lane.env"  # type: ignore[operator]
    secrets_file.write_text("OTHER=1\nLLAMA_EMBED_KEY=local-file-key\n", encoding="utf-8")
    monkeypatch.setattr(local, "KEY_FILES", (str(secrets_file),), raising=False)
    monkeypatch.setenv("LLAMA_EMBED_KEY", "local-stale-key")
    local._KEY.clear()
    assert local.local_key() == "local-file-key"


def test_local_lane_is_parsed_as_env_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """The local credential file is ``KEY=value`` — never returned raw."""
    secrets_file = tmp_path / "embed-lane.env"  # type: ignore[operator]
    secrets_file.write_text("LLAMA_EMBED_KEY=abc123\n", encoding="utf-8")
    monkeypatch.setattr(local, "KEY_FILES", (str(secrets_file),), raising=False)
    monkeypatch.delenv("LLAMA_EMBED_KEY", raising=False)
    local._KEY.clear()
    assert local.local_key() == "abc123"
    assert embedder_providers is not None
