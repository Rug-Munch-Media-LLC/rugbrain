"""``rugbrain.app`` — FastAPI application assembly.

One job per module:

- ``config``        — read + validate process configuration from the env
- ``health_state``  — own the mutable health dict (no I/O)
- ``probes``        — probe Qdrant/Redis/schema, return bools
- ``auth``          — ``X-API-Key`` authentication, fail closed
- ``cors``          — build the CORS middleware from config
- ``boot_registry`` — data table of background subsystems
- ``lifespan``      — compose startup/shutdown from the table
- ``health_routes`` — serve ``/health`` and ``/ready``
- ``routes_registry`` — declare the HTTP surface once
"""
