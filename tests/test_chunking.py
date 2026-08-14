from pathlib import Path

import yaml


def test_chunking_config_loads():
    path = Path(__file__).parent.parent / "src" / "rugbrain" / "chunking_config.yaml"
    with open(path) as f:
        config = yaml.safe_load(f)
    assert "rag_chunking" in config
    for _name, cfg in config["rag_chunking"].items():
        assert cfg["chunk_size"] > 0
        assert 0 <= cfg["overlap"] <= 1
