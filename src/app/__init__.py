"""Self-contained compatibility layer for rmi-rag endpoints.

rmi-rag exposes several API endpoints that historically imported helper
modules from the (internal) rmi-backend package, e.g.::

    from app.confidence import score_confidence          # /rag/confidence
    from app.deployer_reputation import score_deployer_risk  # /scanner/deployer-risk
    from app.investigation_narratives import investigate  # /rag/investigate

The rmi-rag container is standalone and never ships the rmi-backend
``app`` package, so those endpoints 500'd with
``ModuleNotFoundError: No module named 'app'``.

This package provides drop-in, self-contained implementations of the
small subset of ``app.*`` modules the rmi-rag API depends on. They are
implemented on top of rmi-rag's own stack (QdrantVectorStore, the
bge-m3 embedder, httpx LLM calls) so the container stays standalone.

.. note::
    Keep this layer thin. Do not vendor large rmi-backend modules here;
    implement only what the public rmi-rag endpoints need.
"""
