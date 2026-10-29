"""Sentiment engine internals — one job per module.

Split from the 669-line ``sentiment_engine.py``. The public module keeps the
``__all__`` surface and re-exports every name, so existing import paths hold.

* :mod:`.config` — collection name.
* :mod:`.lexicon` — keyword patterns and weights.
* :mod:`.models` — result dataclasses.
* :mod:`.scoring` — synchronous text scoring.
* :mod:`.aggregate` — token/market aggregation.
* :mod:`.routes` — ``sentiment_router`` and its handlers.
"""

from __future__ import annotations
