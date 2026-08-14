"""
RugBrain — CLI entry point.

Usage:
    python -m rugbrain           # Start FastAPI dev server (default)
    python -m rugbrain --ingest  # Run threat ingest pipeline
    python -m rugbrain --help    # Show help
"""

import argparse


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="RugBrain — hybrid search, embeddings, chunking, agentic retrieval"
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Run threat ingest pipeline instead of starting the API server",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",  # noqa: S104  # nosec B104 (intentional API bind)
        help="Bind host for the API server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8012,
        help="Bind port for the API server (default: 8012)",
    )
    args = parser.parse_args()

    if args.ingest:
        # Run threat ingest — run as __main__ so __name__ block fires
        import runpy

        runpy.run_path("scripts/ingest_threats.py", run_name="__main__")
        return

    # Start API server
    import uvicorn

    uvicorn.run(
        "rugbrain.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    _main()
