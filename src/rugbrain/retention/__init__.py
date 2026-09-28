"""Knowledge-retention loop — verdicts become durable, corroborated memory.

The gap this closes: ``record_scanner_result()`` has zero callers, so a
correct scanner verdict was written to a Redis float and lost. There is no
``memory_events`` store and ``scanner_feedback`` does not exist.

The loop is four stages, each a pure decision function over its inputs so the
policy is unit-testable without any backend::

    verdict → observation (durable, retrievable)
            → signature (only when independently corroborated)
            → decay (when a promoted signature stops being supported)

**Anti-poisoning is the point.** A single attacker must never be able to teach
the detector a false signature, so promotion requires independent corroboration
from distinct producers, or an explicit human sign-off. See
:mod:`rugbrain.retention.promotion`.
"""

__all__ = ["decay", "promotion", "signatures"]
