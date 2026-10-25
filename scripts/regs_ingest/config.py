"""Constants for the official-regulator corpus ingester."""

from __future__ import annotations

import os

COLLECTION = "regulation_enforcement_8b"
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
SF_EMBED_URL = "https://api.siliconflow.com/v1/embeddings"
SF_MODEL = "Qwen/Qwen3-Embedding-8B"
LICENCE = "public-domain-us-federal (17 USC 105)"
UA = "rugbrain-editorial-research/1.0 (editorial@rugmunch.io)"
KEY_FILE = "/srv/secrets/siliconflow_api_key"

FEEDS = [
    ("sec_press", "SEC", "https://www.sec.gov/news/pressreleases.rss", 70),
    ("sec_litigation", "SEC", "https://www.sec.gov/enforcement-litigation/litigation-releases/rss", 50),
    ("cftc_press", "CFTC", "https://www.cftc.gov/PressRoom/PressReleases/rss", 50),
    ("fed_press", "FederalReserve", "https://www.federalreserve.gov/feeds/press_all.xml", 50),
]

CFTC_ROBOTS = "https://www.cftc.gov/robots.txt"
CFTC_CANDIDATES = [
    "https://www.cftc.gov/PressRoom/PressReleases/rss",
    "https://www.cftc.gov/PressRoom/PressReleases/rss.xml",
    "https://www.cftc.gov/rss/pressreleases.xml",
]

# (url template, href fragments that mark an article link, agency)
LISTING_PAGES = {
    "sec_press": [("https://www.sec.gov/news/pressreleases?page={n}",
                   ("/news/press-release/", "/news/pressreleases/"), "SEC")],
    "sec_litigation": [("https://www.sec.gov/enforcement-litigation/litigation-releases?page={n}",
                        ("/litigation-releases/lr-",), "SEC")],
    "fed_press": [("https://www.federalreserve.gov/newsevents/pressreleases.htm?page={n}",
                   ("/newsevents/pressreleases/",), "FederalReserve")],
    "cftc_press": [("https://www.cftc.gov/PressRoom/PressReleases?page={n}",
                    ("/PressRoom/PressReleases/",), "CFTC")],
}

PROBE_QUERIES = [
    "What did the SEC allege about unregistered securities against a crypto exchange?",
    "What did the SEC charge about offering unregistered securities in a crypto lending case?",
    "What did the Federal Reserve say about stablecoin reserve custody and bank involvement?",
]
