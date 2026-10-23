"""The touched modules must log or re-raise every except handler."""

import ast
import re
from pathlib import Path

MODULES = ("provider_breaker.py", "market_health.py", "pii_scrubber.py")


def test_no_silent_except_handlers() -> None:
    bad = []
    for name in MODULES:
        path = Path("src/rugbrain") / name
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if not re.search(r"log(ger)?\.(debug|info|warning|error|critical|exception)\(", segment) and "raise" not in segment:
                bad.append(f"{name}:{node.lineno}")
    assert bad == [], f"silent handlers: {bad}"
