"""URL ingestion: markdown fixture, robots/paywall refusals, size cap, 3-URL e2e."""

from __future__ import annotations

import hashlib
import json

from rugbrain.ingest.from_url import UrlPayload, ingest_url
from rugbrain.ingest.url_guards import check_url_allowed
from rugbrain.ingest.url_markdown import fallback_markdown, to_markdown
from rugbrain.ingest.url_search import search_url_corpus
from rugbrain.ingest.url_store import store_records

PROSE = "# Chunking\n\n" + "Token aware chunking keeps addresses and hashes whole. " * 12
BODY = (b"<html><body><h1>Chunking</h1><p>"
        + b"Token aware chunking keeps addresses and hashes whole. " * 12 + b"</p></body></html>")
ROBOTS_OK = "User-agent: *\nAllow: /\n"
ROBOTS_DENY = "User-agent: *\nDisallow: /\n"


def _vec(text: str, dim: int = 4096) -> list[float]:
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    vector = [0.0] * dim
    for i in range(8):
        vector[(seed >> (i * 4)) % dim] += 1.0
    vector[0] += 0.01
    return vector


def test_markdown_conversion_fixture():
    assert "Chunking" in to_markdown(BODY, "text/html")
    assert "addresses and hashes whole" in fallback_markdown(BODY)


def test_robots_refusal():
    verdict = check_url_allowed("https://docs.example/a", BODY, robots_text=ROBOTS_DENY)
    assert (verdict.allowed, verdict.reason) == (False, "robots_disallowed")
    assert check_url_allowed("https://docs.example/a", BODY, robots_text=ROBOTS_OK).allowed


def test_paywall_marker_refused():
    paid = b"<html><body><p>Subscribe to continue reading this report.</p></body></html>"
    assert check_url_allowed("https://news.example/x", paid, content_type="text/html").reason == "paywall"


def test_size_cap_refused():
    big = b"<p>" + b"x" * 4096 + b"</p>"
    assert check_url_allowed("https://docs.example/big", big, max_bytes=1024).reason == "too_large"


async def test_ingest_url_size_cap_envelope(tmp_path):
    async def fetch(_url):
        return UrlPayload(200, "text/html", BODY, ROBOTS_OK)

    async def store(records):
        return await store_records(records, tmp_path / "corpus.jsonl")

    report = await ingest_url(
        "https://docs.example/a", fetch=fetch, store=store,
        embed=_vec_async, max_bytes=256,
    )
    assert (report["ok"], report["refusal"]) == (False, "too_large")


async def _vec_async(texts: list[str]) -> list[list[float]]:
    return [_vec(text) for text in texts]


async def test_three_url_end_to_end(tmp_path):
    urls = ["https://docs.example/a", "https://docs.example/b", "https://docs.example/c"]

    async def fetch(_url):
        return UrlPayload(200, "text/html", BODY, ROBOTS_OK)

    def convert(_body, _ctype, url):
        return PROSE + f"\n\nsource {url}"

    async def store(records):
        return await store_records(records, tmp_path / "corpus.jsonl")

    for url in urls:
        report = await ingest_url(url, fetch=fetch, convert=convert, embed=_vec_async, store=store)
        assert report["ok"], report
        assert report["provenance"]["licence_unknown"] is True
        assert report["provenance"]["url"] == url

    path = tmp_path / "corpus.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert {row["url"] for row in rows} == set(urls)
    probe = next(row for row in rows if "source https://docs.example/b" in row["text"])
    hits = search_url_corpus(_vec(probe["text"]), path=path, top_k=3)
    assert hits[0]["url"] == "https://docs.example/b"
    assert hits[0]["provenance"]["licence_unknown"] is True
