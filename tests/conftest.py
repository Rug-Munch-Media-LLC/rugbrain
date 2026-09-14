"""Hermetic test defaults.

Never let unit tests reach live network lanes: the hosted DeepInfra rerank
rung is disabled unless a test explicitly enables it (2026-09-14 — the key
file exists on dev hosts, which silently activated real DeepInfra calls in
tests).  Tests that exercise the hosted rung monkeypatch the module
functions directly, so they are unaffected.
"""

from __future__ import annotations

import os

os.environ.setdefault("RERANK_HOSTED", "0")
