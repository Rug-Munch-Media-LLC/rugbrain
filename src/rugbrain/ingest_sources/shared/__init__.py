"""Shared ingest layers: fetch, normalise, store, registry.

An ingest source imports from here and nowhere lower. The architecture gate
(``tests/test_arch_ingest_layers.py``) enforces it: no source imports
``httpx``/``aiohttp``/``requests`` or ``qdrant``/``rugbrain.qdrant_store``
directly; the frozen legacy list only ever shrinks.
"""
