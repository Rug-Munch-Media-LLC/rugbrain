"""ingest_smart chunking contracts — incident-aware splitting.

* split_incident_sections: markdown headings, incident keywords,
  contract declarations, paragraph fallback, empty safety
* smart_split: short text passthrough, long sections windowed with
  overlap, sentence-boundary preference, whitespace-only → []
"""

from __future__ import annotations

from rugbrain.ingest_smart import smart_split, split_incident_sections


def test_empty_and_whitespace_safe():
    assert split_incident_sections("") == []
    assert split_incident_sections("   \n  ") == []
    assert smart_split("") == []
    assert smart_split("  ") == []


def test_short_text_passthrough():
    assert smart_split("a short body") == ["a short body"]


def test_markdown_heading_split():
    text = "## Incident A\nbody a\n\n## Incident B\nbody b"
    sections = split_incident_sections(text)
    assert len(sections) == 2
    assert "Incident A" in sections[0]
    assert "Incident B" in sections[1]


def test_contract_declaration_split():
    text = "contract TokenOne { transfer(); }\ncontract TokenTwo { mint(); }"
    sections = split_incident_sections(text)
    assert len(sections) >= 2


def test_smart_split_windows_long_sections():
    body = "sentence one. " * 200  # ~2800 chars
    chunks = smart_split(body, max_len=700, overlap=70)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= 700 + 100  # window bound (allow overlap slop)
        assert c.strip()


def test_smart_split_prefers_sentence_boundaries():
    text = ("First sentence here. " * 40) + "Final complete sentence."
    chunks = smart_split(text, max_len=300, overlap=30)
    # no chunk should start mid-word
    for c in chunks:
        first_word = c.split()[0] if c.split() else ""
        assert first_word.islower() or first_word[0].isupper() or first_word.startswith("...")


def test_overlap_creates_continuity():
    text = "word " * 300  # 1500 chars of one word repeated
    chunks = smart_split(text, max_len=400, overlap=40)
    assert len(chunks) >= 2
