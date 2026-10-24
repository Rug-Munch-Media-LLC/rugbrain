"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

YouTube scam watch — bounded monitoring of crypto channels for promoted tokens.

The package produces **article briefs**, never publications. Every brief is a
``draft`` that says ``alleged`` and carries legal-safety notes, and it can only
exist after :mod:`rugbrain.scam_watch.verify` found two independent sources
(one of them on-chain). A heuristic alone can open an investigation; it can
never open a story.
"""

from rugbrain.scam_watch.brief import InsufficientEvidence, build_brief
from rugbrain.scam_watch.dedupe import dedupe_brief
from rugbrain.scam_watch.models import (
    ArticleBrief,
    ChannelRecord,
    EvidenceItem,
    Signal,
    Verdict,
    VideoMeta,
)
from rugbrain.scam_watch.scan import ScanReport, run_scan
from rugbrain.scam_watch.signals import detect_signals
from rugbrain.scam_watch.verify import assess
from rugbrain.scam_watch.watchlist import load_watchlist

__all__ = [
    "ArticleBrief",
    "ChannelRecord",
    "EvidenceItem",
    "InsufficientEvidence",
    "ScanReport",
    "Signal",
    "Verdict",
    "VideoMeta",
    "assess",
    "build_brief",
    "dedupe_brief",
    "detect_signals",
    "load_watchlist",
    "run_scan",
]
