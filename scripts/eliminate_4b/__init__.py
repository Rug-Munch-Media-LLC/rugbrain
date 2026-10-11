"""Fill the _8b twins so the 2560 bases can be deleted.

OPERATOR DECISION (2026-09-29): Qwen3-8B / 2560 is ELIMINATED. Every base must
reach parity with its 4096 twin before the cut; then only 4096 survives.

THIS MODULE DELETES NOTHING. It fills. The cut is a separate phase, owned by
someone who verifies first.
"""
