#!/usr/bin/env python3
"""One-shot SEC EDGAR filing ingestion.

Run daily via cron: 0 6 * * * cd /srv/work/repos/rmi-rag && docker compose run --rm rag-ingest python scripts/ingest_sec_edgar.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.sec_edgar import ingest_all_crypto_filings


async def main():
    print("SEC EDGAR ingestion starting...")
    result = await ingest_all_crypto_filings()
    print(f"Found: {result['total_found']} filings")
    print(f"Companies: {result['companies']}")
    if result["errors"]:
        print(f"Errors: {len(result['errors'])}")
        for e in result["errors"][:3]:
            print(f"  {e}")


if __name__ == "__main__":
    asyncio.run(main())
