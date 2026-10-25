"""Chunking config is enforced, and boundaries never cut a word."""

from itertools import pairwise
from pathlib import Path

import pytest

from rugbrain.chunk_config import (
    CONFIG_PATH,
    ChunkingConfigError,
    chunk_settings,
    load_chunking_config,
)
from rugbrain.chunking_boundaries import chunk_spans
from rugbrain.ingest_common import chunk_text


def test_config_loads_and_is_validated() -> None:
    cfg = load_chunking_config(CONFIG_PATH)
    assert "default" in cfg
    for settings in cfg.values():
        assert settings.chunk_size > 0
        assert 0 <= settings.overlap <= 1
        assert settings.max_chars >= settings.chunk_size


def test_news_entry_matches_yaml() -> None:
    assert chunk_settings("news").chunk_size == 2048
    assert chunk_settings("news").overlap == pytest.approx(0.15)


def test_unknown_collection_fails_loudly() -> None:
    with pytest.raises(ChunkingConfigError, match="no chunking entry"):
        chunk_settings("does_not_exist")


def test_unreadable_config_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ChunkingConfigError, match="unreadable"):
        load_chunking_config(tmp_path / "missing.yaml")


def test_invalid_config_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("rag_chunking:\n  default:\n    chunk_size: 100\n    overlap: 1.5\n    max_chars: 50\n")
    with pytest.raises(ChunkingConfigError, match=r"overlap|max_chars"):
        load_chunking_config(bad)


def test_missing_default_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "no_default.yaml"
    bad.write_text("rag_chunking:\n  news:\n    chunk_size: 100\n    overlap: 0.1\n    max_chars: 120\n")
    with pytest.raises(ChunkingConfigError, match="default"):
        load_chunking_config(bad)


def _sentences(n: int, width: int = 40) -> str:
    return " ".join(f"Sentence {i} " + "word " * (width // 5) + "end." for i in range(n))


def test_boundaries_land_on_sentence_ends() -> None:
    text = _sentences(30)
    for start, end in chunk_spans(text, 300, 0.15, 400):
        assert text[end - 1] in ".!?… \n", repr(text[max(0, end - 20):end])
        body = text[start:end].strip()
        assert body.endswith("."), repr(body[-30:])


def test_never_splits_mid_word() -> None:
    text = _sentences(40)
    for start, end in chunk_spans(text, 250, 0.1, 350):
        if end < len(text):
            assert not (text[end - 1].isalnum() and text[end].isalnum())


def test_oversize_paragraph_falls_back_to_cap_at_space() -> None:
    text = "word " * 600  # no sentence enders, no newlines
    spans = chunk_spans(text, 300, 0.0, 400)
    assert len(spans) > 1
    for start, end in spans:
        assert end - start <= 400
        if end < len(text):
            assert text[end - 1] == " ", repr(text[max(0, end - 10):end + 5])


def test_no_chunk_below_minimum_length() -> None:
    text = _sentences(30) + "tiny."
    spans = chunk_spans(text, 400, 0.0, 480, min_chars=60)
    assert len(spans) > 1
    for start, end in spans:
        assert len(text[start:end].strip()) >= 60


def test_overlap_keeps_continuity() -> None:
    text = _sentences(30)
    spans = chunk_spans(text, 400, 0.15, 500)
    assert len(spans) > 1
    for (_, prev_end), (next_start, _) in pairwise(spans):
        assert next_start < prev_end
        assert text[next_start - 1] == " " or text[next_start].isalnum()


def test_chunk_text_uses_collection_config() -> None:
    text = _sentences(80)
    news = chunk_text(text, collection="news")
    assert all(len(c) <= chunk_settings("news").max_chars for c in news)
    assert chunk_text("", collection="news") == []


def test_chunk_text_cut_is_fixture_visible() -> None:
    """The old hard slice cut 'for c|ross-chain' — the new one does not."""
    prose = ("architecture " * 90) + "with native support for cross-chain interoperability. "
    prose += "The design " + ("keeps state " * 120)
    chunks = chunk_text(prose, size=1500, overlap=100, max_chars=1600)
    joined = " ".join(chunks)
    assert "for cross-chain interoperability" in joined
    for chunk in chunks:
        assert not chunk.endswith("c"), repr(chunk[-20:])


def test_unbreakable_token_cuts_at_cap_and_keeps_overlap() -> None:
    text = "a" * 5000  # no whitespace at all: no word boundary to respect
    chunks = chunk_text(text, size=1000, overlap=100)
    assert len(chunks) >= 5
    assert all(len(c) <= 1000 for c in chunks)
    assert chunks[0][-100:] == chunks[1][:100]


def test_ingest_unknown_collection_is_a_loud_error() -> None:
    with pytest.raises(ChunkingConfigError):
        chunk_text("word " * 400, collection="not_configured")
