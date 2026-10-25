import pytest

from rugbrain.embed_lane import EmbedCredentialError, embed_selfcheck, resolve_key


def test_missing_key_names_env_var_and_endpoint(monkeypatch):
    monkeypatch.delenv("EMBED_LOCAL_API_KEY", raising=False)
    monkeypatch.setenv("EMBED_LANE_KEY_FILE", "/nonexistent/embed-lane.env")
    with pytest.raises(EmbedCredentialError) as err:
        embed_selfcheck("local")
    assert "EMBED_LOCAL_API_KEY" in str(err.value)
    assert "8024" in str(err.value)


def test_resolve_key_prefers_env(monkeypatch):
    monkeypatch.setenv("EMBED_LOCAL_API_KEY", "from-env")
    from rugbrain.embed_lane import LANES
    assert resolve_key(LANES["local"]) == "from-env"
