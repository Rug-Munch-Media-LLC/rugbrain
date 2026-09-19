"""Runtime toggle shared by the guardrail submodules."""

from __future__ import annotations

import logging
import os

# Package-level logger kept at the canonical ``rugbrain.guardrails`` name so
# log records are identical to the pre-split single-module implementation.
logger = logging.getLogger("rugbrain.guardrails")

# Set RMI_GUARDRAILS=0 to disable input sanitization / output redaction
# (used by tests and local debugging). Trusted filtering is always opt-in
# because it requires callers to pass metadata-bearing docs.
_GUARDRAILS_ENABLED = os.getenv("RMI_GUARDRAILS", "1").strip().lower() not in ("0", "false", "no")
