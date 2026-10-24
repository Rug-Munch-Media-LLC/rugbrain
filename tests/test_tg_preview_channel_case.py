"""Channel handle case is normalised at the ``t.me/s`` parse boundary.

Regression for the 2026-10-24 content audit: ``data-post`` carries Telegram's
canonical handle (``WatcherGuru``) while the sweep list and ``tgweb_state.json``
key the same channel in lower case (``watcherguru``). The stored ``source``
payload therefore read ``t.me/WatcherGuru``, so every source-keyed lookup
(per-channel counts, provenance joins, per-channel ingest reports) returned a
silent zero for a channel that had 36 points in ``social_intel_8b``.

Telegram handles are case-insensitive, so the parser lowercases them once, at
the boundary, instead of every consumer re-deriving a handle spelling.
"""

from __future__ import annotations

import pytest

from rugbrain.ingest_sources.tg_preview import parse_preview_page

BODY = (
    '<div class="tgme_widget_message" data-post="{handle}/101">'
    '<div class="tgme_widget_message_text">A sufficiently long crypto scam alert body.</div>'
    '<div class="tgme_widget_message_footer"></div></div>'
)


@pytest.mark.parametrize(
    ("handle", "expected"),
    [
        ("WatcherGuru", "watcherguru"),  # canonical case from data-post
        ("ALSTEIN_GEMCLUB", "alstein_gemclub"),  # upper-case handle
        ("news_crypto", "news_crypto"),  # already lower, must not change
        ("HopiumCasino", "hopiumcasino"),
    ],
)
def test_preview_channel_is_lowercased(handle: str, expected: str) -> None:
    msgs = parse_preview_page(BODY.format(handle=handle), handle)

    assert [m["channel"] for m in msgs] == [expected]
