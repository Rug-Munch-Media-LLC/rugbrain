"""Chunking contract for ``scripts/ingest_security_knowledge.py``.

The ingest is judged on one property: a finding is never torn apart. These
tests pin the two mechanisms that deliver it — finding-heading segmentation
(an ``H-01`` block keeps its Impact/PoC/Recommendation together) and the
fence-aware packer (an open code fence is never cut, however long it is).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.ingest_sources import audit_chunking as mod

_REPORT = """# Audit report: Vault

## Findings

### [H-01] Reentrancy in withdraw

Impact: funds drained.

```solidity
function withdraw() external {
    // a comment that is not a heading
    msg.sender.call{value: balance}("");
}
```

Recommendation: follow checks-effects-interactions.

### [M-02] Missing zero-address check

Impact: funds sent to address(0).

### [L-03] Unused import
"""


def test_units_split_on_finding_headings() -> None:
    units = mod._units(_REPORT)
    ids = [u[0] for u in units]
    assert ids[0] == "context"
    assert "H01" in ids and "M02" in ids and "L03" in ids
    high = next(u for u in units if u[0] == "H01")
    assert "Impact: funds drained." in high[2]
    assert "Recommendation: follow checks-effects-interactions." in high[2]
    assert "Reentrancy in withdraw" in high[1]
    assert any("Missing zero-address check" in u[1] for u in units)


def test_heading_inside_fence_is_not_a_finding() -> None:
    units = mod._units(_REPORT)
    high = next(u for u in units if u[0] == "H01")
    assert "a comment that is not a heading" in high[2]
    assert not any(u[1].startswith("a comment") for u in units)


def test_pack_never_cuts_an_open_fence() -> None:
    body = "intro\n```solidity\n" + ("uint256 x = 1;\n" * 400) + "```\noutro\n"
    pieces = mod._pack(body)
    assert len(pieces) > 1
    for piece in pieces:
        assert piece.count("```") % 2 == 0, "a fence was cut mid-block"


def test_severity_mapping() -> None:
    assert mod._severity_of("[C-01] total loss") == "critical"
    assert mod._severity_of("H-04 thing") == "high"
    assert mod._severity_of("### [M-02] missing check") == "medium"
    assert mod._severity_of("Low risk note") == "low"
    assert mod._severity_of("Informational") == "info"
