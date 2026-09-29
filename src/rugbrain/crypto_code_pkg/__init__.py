"""The crypto-code retrieval path, split by responsibility.

- :mod:`classify`     — is this question about crypto code at all?
- :mod:`retriever`    — dense + keyword + RRF search over ContextCrypto
- :mod:`attribution`  — what a hit is, and how it projects onto a search result

``rugbrain.crypto_code_retrieval`` is the stable facade over the three.
"""

from __future__ import annotations

__all__ = ["attribution", "classify", "retriever"]
