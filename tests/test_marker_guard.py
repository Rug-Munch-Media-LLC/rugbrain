"""Guard: a test module that can reach the network must carry the ``live`` marker.

Kept independent of conftest.classify on purpose - the guard is a second
opinion (imports + URL literals), so a bug in the marker rules cannot hide
behind the same bug in the check.
"""
from __future__ import annotations

import ast
import pathlib

_SCHEMES = ("ht" + "tp://", "ht" + "tps://")
_NET_MODULES = {
    "requests", "httpx", "aiohttp", "urllib", "urllib3",
    "socket", "websockets", "http", "pycurl",
}
_cache: dict[str, bool] = {}


def _touches_network(path: pathlib.Path, src: str | None = None) -> bool:
    key = str(path)
    if src is None and key in _cache:
        return _cache[key]
    text = src if src is not None else path.read_text(encoding="utf-8")
    hit = False
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            hit = hit or any(a.name.split(".")[0] in _NET_MODULES for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            hit = hit or (node.module or "").split(".")[0] in _NET_MODULES
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            hit = hit or any(s in node.value for s in _SCHEMES)
    if src is None:
        _cache[key] = hit
    return hit


def test_every_network_test_is_marked_live(request):
    offenders = [
        item.nodeid
        for item in request.session.items
        if not item.get_closest_marker("live")
        and _touches_network(pathlib.Path(str(item.path)))
    ]
    assert not offenders, (
        "these tests can reach the network but lack @pytest.mark.live:\n  "
        + "\n  ".join(offenders[:20])
    )


def test_detector_recognises_a_network_literal(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text('URL = "ht' + 'tps://example.test"\n', encoding="utf-8")
    assert _touches_network(probe)
