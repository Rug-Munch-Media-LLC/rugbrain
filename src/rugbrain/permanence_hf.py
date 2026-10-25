"""HuggingFace Storage Buckets mirror for RAG permanence (opt-in).

Split out of :mod:`rugbrain.permanence` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from rugbrain.permanence_config import HF_REPO, HF_TOKEN

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# HUGGINGFACE STORAGE BUCKETS MIRROR
# ═══════════════════════════════════════════════════════════════
def _hf_put(key: str, data: bytes) -> bool:
    """Upload an object to HuggingFace Storage Buckets via hf CLI.

    Writes data to a temp file and calls `hf storage buckets upload`.
    Uses HF_TOKEN env var for authentication.
    """
    hf_token = HF_TOKEN if HF_TOKEN else os.getenv("HF_TOKEN", "")

    if not hf_token:
        logger.error("HF_TOKEN not set")
        return False

    hf_key = f"rag/{key}"

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        try:
            tmp.write(data)
            tmp.flush()
            result = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "hf",
                    "storage",
                    "buckets",
                    "upload",
                    "--repo",
                    HF_REPO,
                    "--key",
                    hf_key,
                    "--token",
                    hf_token,
                    tmp.name,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True
            logger.error("HF PUT %s: %s %s", key, result.returncode, result.stderr[:200])
            return False
        except Exception as e:
            logger.error("HF PUT %s: %s", key, e)
            return False
        finally:
            with contextlib.suppress(OSError):
                Path(tmp.name).unlink()
