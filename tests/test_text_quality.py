"""Predicate + extractor tests: a JS shell must never become a document."""

from __future__ import annotations

from rugbrain.page_text import extract_page_text
from rugbrain.text_quality import MAX_SCRIPT_RATIO, MIN_PROSE_WORDS, judge_text

SHELL = (
    "<html><head><script>window.__reactRouterContext={x:1};"
    "gtag('config','G-1');</script>"
    "<script src='https://www.googletagmanager.com/gtm.js'></script></head>"
    "<body><div id='root'></div></body></html>"
)

PROSE = (
    "<html><body><nav>home docs</nav><h1>Provably fair</h1>"
    "<p>Each bet is committed to a server seed hash before the player reveals the client seed, "
    "so the outcome can be recomputed from the published seeds after the round ends and compared "
    "against the hash that was shown in advance. A player can run the same calculation locally, compare it with the commitment posted before the round started, and confirm that the operator did not swap the seed afterwards, which is the whole point of the mechanism.</p>"
    "<script>var a=1;</script></body></html>"
)


def test_react_shell_is_refused() -> None:
    verdict = judge_text(extract_page_text(SHELL))
    assert not verdict.ok
    assert verdict.ok is False and verdict.reason


def test_marker_alone_is_refused() -> None:
    verdict = judge_text("window.__INITIAL_STATE__ " + "lorem ipsum dolor sit amet " * 30)
    assert not verdict.ok and "marker" in verdict.reason


def test_thin_page_is_refused() -> None:
    assert not judge_text("Just a stub with no substance at all here.").ok
    assert judge_text("word " * MIN_PROSE_WORDS).words >= MIN_PROSE_WORDS


def test_prose_survives_and_chrome_is_stripped() -> None:
    text = extract_page_text(PROSE)
    verdict = judge_text(text)
    assert verdict.ok, verdict
    assert "Provably fair" in text
    assert "home docs" not in text
    assert "var a=1" not in text
    assert verdict.script_ratio <= MAX_SCRIPT_RATIO


def test_js_bundle_prose_is_refused() -> None:
    bundle = "\n".join(
        ["// Safari omits the URL: WebKit rejects a failed import()", "const x = load();",
         "export default function y() { return 1; }", "if (a) { b(); }", "};"] * 12
    )
    assert not judge_text(bundle).ok
