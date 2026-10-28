"""Web crawler internals — one job per module.

Split from the 637-line ``web_crawler.py``; the public module keeps the crawl
entry points (so ``monkeypatch.setattr(mod, "fetch_rss_feed", ...)`` keeps
working) and re-exports every other name.

* :mod:`.config` — env knobs, sources, feeds, GitHub queries.
* :mod:`.politeness` — per-domain rate limiter and robots.txt cache.
* :mod:`.html_text` — HTML-to-text fallback extraction.
* :mod:`.crawler` — ``WebCrawler`` (crawl4ai first, httpx fallback).
"""

from __future__ import annotations
