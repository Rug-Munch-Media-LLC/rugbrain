from rugbrain.embeddings import EMBED_MODEL_NAME, EMBED_VERSION_STAMP, EMBEDDING_DIM


def test_embedding_dim():
    assert EMBEDDING_DIM == 2560


def test_model_name():
    assert EMBED_MODEL_NAME == "qwen3-embedding-4b"


def test_version_stamp():
    assert "qwen3-embedding-4b" in EMBED_VERSION_STAMP
    assert "2560" in EMBED_VERSION_STAMP
    assert ":" in EMBED_VERSION_STAMP


def test_embeddings_importable():
    from rugbrain.embeddings import current_backend, get_embedding, get_embeddings

    assert callable(get_embedding)
    assert callable(get_embeddings)
    assert callable(current_backend)
