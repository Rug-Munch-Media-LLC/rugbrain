import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import docs_registry_quality as q


def test_landing_detected():
    assert q.classify_url("https://docs.foo.com/") == "landing"
    assert q.classify_url("https://docs.foo.com/index.html") == "landing"


def test_reference_and_guide():
    assert q.classify_url("https://x/api/v1/endpoint") == "reference"
    assert q.classify_url("https://x/guide/getting-started") == "guide"


def test_summarise_majority():
    out = q.summarise(["https://x/"] * 6 + ["https://x/api/a"] * 4)
    assert out["quality"] == "landing" and out["landing_ratio"] == 0.6


def test_validator_requires_provenance():
    errs = q.validate({"library_id": "docs:a", "display_name": "A"})
    assert any("licence" in e for e in errs)
    assert any("fetched_at" in e for e in errs)


def test_validator_flags_landing_debt():
    entry = {"library_id": "docs:a", "display_name": "A", "source_repo": "a/b",
             "source_url": "https://a", "licence": "MIT",
             "fetched_at": "2026-10-22", "chunk_count": 5,
             "quality": "landing", "landing_ratio": 0.9}
    assert "landing-pages-only (debt)" in q.validate(entry)
