"""X/Twitter social-intel ingestion for RugBrain (ported from rugmunchbot).

Two data lanes, one pipeline:

* **GetXAPI** (``api.getxapi.com``, metered ~$0.001/call) — TIER 1/2 accounts
  and keyword search. Budget-gated by :mod:`rugbrain.ingest_sources.x_intel.budget`.
* **Pry** (self-hosted scraper, free) — TIER 3/4 long-tail accounts and any
  request made while pacing is active.

Pipeline (``--cycle``): fetch → normalize → dedupe → embed → Qdrant ``news_v2``
via the shared ``rugbrain.ingest_common.store_documents`` machinery.

Fail-open everywhere: a dead GetXAPI, Pry, or Redis never raises into the
scheduler — callers get ``[]`` / zero counts with a log line.
"""

from __future__ import annotations

from rugbrain.ingest_sources.x_intel.config import api_key_configured, as_dict
from rugbrain.ingest_sources.x_intel.normalize import normalize_profile, normalize_tweet
from rugbrain.ingest_sources.x_intel.pipeline import run_cycle
from rugbrain.ingest_sources.x_intel.routing import route_account, should_use_getxapi

__all__ = [
    "api_key_configured",
    "as_dict",
    "normalize_profile",
    "normalize_tweet",
    "route_account",
    "run_cycle",
    "should_use_getxapi",
]
