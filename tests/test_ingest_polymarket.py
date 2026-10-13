#!/usr/bin/env python3
"""Test for ingest_polymarket."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ingest_polymarket import main

@pytest.mark.asyncio
async def test_main_dry_run():
    """Test dry-run mode."""
    result = await main(dry_run=True, b2_upload=False, limit_files=None)
    assert result["files_processed"] == 21  # 21 parquet files in the dataset
    assert result["errors"] == 0

@pytest.mark.asyncio
async def test_main_b2_creds_missing():
    """Test with B2 creds missing (should warn and skip upload)."""
    result = await main(dry_run=False, b2_upload=True, limit_files=3)
    # Should have warned about missing creds and not uploaded
    assert result["b2_uploaded"] == 0  # No upload without creds

@pytest.mark.asyncio
async def test_main_with_limit():
    """Test with file limit."""
    result = await main(dry_run=True, b2_upload=False, limit_files=3)
    assert result["files_processed"] == 3
    assert result["errors"] == 0

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
