from rugbrain.ingest_sources.crypto_rekts import parse_rektdb


def test_parse_rektdb_envelope():
    text = '{"items": [{"id": 1, "title": "X"}], "count": 1}'
    assert parse_rektdb(text) == [{"id": 1, "title": "X"}]


def test_parse_rektdb_bare_list():
    text = '[{"id": 2, "title": "Y"}]'
    assert parse_rektdb(text) == [{"id": 2, "title": "Y"}]


def test_parse_rektdb_malformed():
    assert parse_rektdb("not json") == []
    assert parse_rektdb("") == []
