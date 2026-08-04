"""Tests for rmi_rag.rss_parser — minimal RSS 2.0 / Atom parser."""

from __future__ import annotations

from rmi_rag.rss_parser import parse_feed, parse_rss

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example Feed</title>
  <item>
    <title><![CDATA[Bitcoin hits new high &amp; breaks record]]></title>
    <link>https://example.com/story/1</link>
    <pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate>
    <description><![CDATA[<p>Bitcoin <b>surges</b> past expectations.</p>]]></description>
  </item>
  <item>
    <title>Second item</title>
    <link>https://example.com/story/2</link>
    <pubDate>Wed, 05 Aug 2026 09:30:00 GMT</pubDate>
    <description>Plain text summary.</description>
  </item>
</channel></rss>"""

ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>EIP-7702: Set EOA account code</title>
    <link href="https://eips.ethereum.org/EIPS/eip-7702"/>
    <published>2026-08-04T10:00:00Z</published>
    <summary>Account abstraction via one-time code setting.</summary>
  </entry>
  <entry>
    <title>EIP-7685</title>
    <link href="https://eips.ethereum.org/EIPS/eip-7685"/>
    <published>2026-08-03T08:00:00Z</published>
    <content>General purpose execution layer requests.</content>
  </entry>
</feed>"""


def test_parse_rss_items_and_entities():
    items = parse_rss(RSS_XML)
    assert len(items) == 2
    assert items[0]["title"] == "Bitcoin hits new high & breaks record"
    assert items[0]["url"] == "https://example.com/story/1"
    assert "surges" in items[0]["summary"]
    assert "<b>" not in items[0]["summary"]
    assert items[0]["published"].startswith("2026-08-04")
    assert items[0]["is_atom"] is False


def test_parse_rss_plain_description():
    items = parse_rss(RSS_XML)
    assert items[1]["summary"] == "Plain text summary."


def test_parse_feed_title():
    feed_title, items = parse_feed(RSS_XML)
    assert feed_title == "Example Feed"
    assert len(items) == 2


def test_parse_atom_entries():
    feed_title, items = parse_feed(ATOM_XML)
    assert feed_title == "Atom Feed"
    assert len(items) == 2
    assert items[0]["title"] == "EIP-7702: Set EOA account code"
    assert items[0]["url"] == "https://eips.ethereum.org/EIPS/eip-7702"
    assert items[0]["is_atom"] is True
    assert items[0]["published"].startswith("2026-08-04")


def test_parse_rss_empty_and_garbage():
    assert parse_rss("") == []
    assert parse_rss("<html><body>not a feed</body></html>") == []
    feed_title, items = parse_feed("<not-a-feed/>")
    assert feed_title == ""
    assert items == []


def test_parse_rss_limit():
    items = parse_rss(RSS_XML, limit=1)
    assert len(items) == 1
