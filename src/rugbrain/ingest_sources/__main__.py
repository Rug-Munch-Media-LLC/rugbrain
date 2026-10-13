"""Programmatic data-source ingest CLI.

The documented invocation is ``python -m rugbrain.ingest_sources --source X``.
It imported ``_main``, which does not exist, so the entrypoint raised ImportError
and the ingest CLI was unreachable — the tool that populates collections could
only be driven by ad-hoc imports.
"""

from rugbrain.ingest_sources.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
