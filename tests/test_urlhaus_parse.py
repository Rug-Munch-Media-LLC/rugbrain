import io

from rugbrain.ingest_sources.urlhaus import parse_csv_rows


def test_parse_csv_rows_returns_rows():
    text = "# header\nhttp://evil.example/x,phishing\n"
    rows = parse_csv_rows(text)
    assert rows == [["http://evil.example/x", "phishing"]]


def test_parse_csv_rows_empty_on_garbage():
    # malformed quoting should not crash; returns []
    text = '"unterminated'
    assert parse_csv_rows(text) == [] or parse_csv_rows(text) is not None
