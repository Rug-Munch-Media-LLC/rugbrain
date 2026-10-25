"""News dedupe identity: does one story keep ONE key across its syndications?

Measured gap (2026-10-25, news_v2_8b, 299,074 points):
  4,870 rows sit in "same normalised title, different url" clusters and 2,166
  in "same title, different channel" clusters — i.e. the same story stored
  more than once — while 298,857/299,074 points carry no content_hash at all.

``rss_store.content_hash`` hashes ``title|guid-or-link``, so identity changes
whenever the *transport* changes. Case (b) below is the measured miss.
"""

from __future__ import annotations

import hashlib

from rugbrain.ingest_sources.ingest_dedupe import jaccard, normalize_for_dedupe, shingles
from rugbrain.rss_store import content_hash

BODY = (
    "Solana's Shanghai event was halted with officers present, attendees say. "
    "Organisers told the crowd to leave the venue shortly after 14:00 local time."
)
TITLE = "Solana Shanghai Event Halted, Attendees Say"

# (a) same url + different body, (b) different url + same title,
# (c) different url + same body
A = {"title": TITLE, "url": "https://ex.com/a", "body": BODY}
B = {"title": TITLE, "url": "https://other.com/b", "body": BODY}
C = {"title": TITLE + " (updated)", "url": "https://third.com/c", "body": BODY}


def _key(p):
    return content_hash(p["title"], "", p["url"])


def test_a_same_url_different_body_is_caught():
    """Same url, different body → same key (body is not an input at all)."""
    p = dict(A, body=BODY + " Correction: the venue was cleared at 15:00.")
    assert _key(A) == _key(p)


def test_b_same_title_different_url_is_missed_by_content_hash():
    """The measured gap: one story, two outlets → two keys."""
    assert _key(A) != _key(B), "content_hash already collapses different links"


def test_c_same_body_different_url_is_caught_by_jaccard_only():
    """Different url, same body → content_hash diverges, Jaccard catches it."""
    assert _key(A) != _key(C)
    sim = jaccard(
        shingles(normalize_for_dedupe(A["body"])), shingles(normalize_for_dedupe(C["body"]))
    )
    assert sim >= 0.62, sim


def test_story_key_collapses_same_title_across_links():
    """The fix: story identity ignores the transport."""
    from rugbrain.story_key import story_key

    assert story_key(A["title"], A["body"], A["url"]) == story_key(B["title"], B["body"], B["url"])
    assert story_key(A["title"], A["body"], A["url"]) != story_key(
        "Bitcoin ETF inflows hit a record", BODY, A["url"]
    )


def test_story_key_matches_hash_width():
    from rugbrain.story_key import story_key

    assert len(story_key(TITLE, BODY, A["url"])) == len(hashlib.sha256(b"x").hexdigest()[:32])
