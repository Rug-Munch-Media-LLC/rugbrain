"""Prompt compression internals — one job per module.

Split from the 734-line ``prompt_compression.py``; the public module keeps
``compress_context`` / ``layout_context`` / ``pack_context`` and re-exports
every name, so all existing import paths are unchanged.

* :mod:`.signals` — token estimate, regexes/lexicon, ``_PromptUnit``, scoring.
* :mod:`.units` — extract / slice / truncate / select / compress text.
* :mod:`.llmlingua_lane` — the optional LLMLingua lane (lazy, probed once).
* :mod:`.chunks` — per-chunk budgets, text accessors, annotation, score lookup.
"""

from __future__ import annotations
