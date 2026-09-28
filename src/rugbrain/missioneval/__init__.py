"""Crypto-crime mission eval — graded relevance over the 7 mission job classes.

The pre-existing ``rugbrain.eval`` harness measures *retrieval mechanics* over
a 50-row golden set that is 40/50 code-retrieval and 0/50 ``provenance=verified``.
It answers "does the plumbing rank things" — not "can this system spot a rug".

This package answers the second question with graded labels
(``gold`` / ``partial`` / ``off-topic``) so partial credit is visible, and it is
wired to a hard gate that FAILS a regression.
"""

__all__ = ["cases", "goldens", "judge", "runner"]
