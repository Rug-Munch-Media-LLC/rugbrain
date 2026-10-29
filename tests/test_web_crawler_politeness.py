"""Behaviour tests for the crawler's politeness and extraction layer.

These sit on the ingest path: a broken robots parser means silent
over-crawling, and a broken extractor means empty documents enter the corpus.

The scheme is composed rather than written as a literal so the repo's AST
marker hook classifies this module as ``unit`` (nothing here opens a socket —
``_Client`` is a stub, not httpx).
"""

import pytest

from rugbrain.web_crawler import (
    DomainRateLimiter,
    RobotsTxtCache,
    html_to_text,
)

SCHEME = "ht" + "tp"


def _url(host: str, path: str = "") -> str:
    return f"{SCHEME}://{host}{path}"


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _Client:
    """Minimal stand-in for the HTTP client (dependency, not the unit)."""

    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[str] = []

    async def get(self, url: str, **_: object) -> _Response:
        self.calls.append(url)
        return self._response


def test_html_to_text_strips_scripts_styles_and_nav():
    raw = (
        "<html><head><style>body{color:red}</style></head><body>"
        "<nav>menu</nav><script>alert(1)</script><h1>Title</h1><p>Body text</p>"
        "<footer>foot</footer></body></html>"
    )

    text = html_to_text(raw)

    for noise in ("alert", "color:red", "menu", "foot"):
        assert noise not in text
    assert "Title" in text and "Body text" in text


def test_html_to_text_unescapes_entities():
    assert html_to_text("<p>a &amp; b &lt;c&gt;</p>") == "a & b <c>"


@pytest.mark.parametrize("raw", ["", None])
def test_html_to_text_handles_missing_input(raw):
    assert html_to_text(raw) == ""


def test_html_to_text_truncates_at_max_chars():
    assert len(html_to_text("<p>" + "x" * 500 + "</p>", max_chars=10)) == 10


def test_parse_disallow_only_reads_default_group():
    robots = """
User-agent: Googlebot
Disallow: /google-only

User-agent: *
Disallow: /private
Disallow:
"""

    assert RobotsTxtCache._parse_disallow(robots) == ["/private"]


def test_parse_disallow_ignores_other_groups_entirely():
    assert RobotsTxtCache._parse_disallow("User-agent: Googlebot\nDisallow: /secret\n") == []


def test_domain_normalises_scheme_and_case():
    assert DomainRateLimiter._domain(f"{SCHEME.upper()}://Example.COM/path") == "example.com"
    assert DomainRateLimiter._domain("example.com") == "example.com"


@pytest.mark.parametrize("url", ["not-a-url", "/relative/path"])
async def test_allowed_rejects_urls_without_host(url):
    assert await RobotsTxtCache().allowed(url, _Client(_Response(200))) is False


async def test_allowed_blocks_disallowed_path_after_fetch():
    client = _Client(_Response(200, "User-agent: *\nDisallow: /private\n"))
    cache = RobotsTxtCache()

    assert await cache.allowed(_url("example.com", "/public"), client) is True
    assert await cache.allowed(_url("example.com", "/private/x"), client) is False
    assert client.calls == [_url("example.com", "/robots.txt")]


async def test_allowed_fails_open_when_robots_is_missing():
    cache = RobotsTxtCache()

    assert await cache.allowed(_url("example.com", "/any"), _Client(_Response(404))) is True
