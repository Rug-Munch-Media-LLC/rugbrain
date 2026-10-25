"""Curated knowledge data as importable package data (no I/O, no side effects).

Sub-packages hold the datasets that used to live in ``scripts/*_knowledge_data.py``
as top-level literals. They are chunked so every module stays under 200 lines and
can be imported by tests, the ingest scripts (via a compat shim) and future
services without a ``sys.path`` hack.
"""
