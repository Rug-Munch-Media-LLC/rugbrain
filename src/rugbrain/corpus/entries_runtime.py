"""Declared non-corpora: runtime state, indices and probes.

These collections exist in Qdrant and are therefore in scope for the UNDECLARED
check, but they are not corpora — they are scanner state, agent memory, a routing
index and two deliberate test probes. Declaring them with ``domain=unclassified``
and an ``internal://runtime-state`` source is what keeps the undeclared report
about corpora: a corpus that lands here by accident is a categorisation bug.

Not coverage. Do not count these when answering "what does the corpus hold".
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.entries_base import ON_DEMAND, PROBE, RUNTIME, D, _d
from rugbrain.corpus.schema import CorpusDeclaration
from rugbrain.qdrant_store.lane_table import PRESERVED_LANE_DIM

_NON_CORPUS: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "agent-memory-state",
        "agent_memory",
        cid="agent_memory",
        domain=D.UNCLASSIFIED,
        url=RUNTIME,
        cadence=ON_DEMAND,
        notes="Runtime state. 4 pts.",
        observed=4096,
        retired=()
    ),
    _d(
        "agent-memory-entities-state",
        "agent_memory_entities",
        cid="agent_memory_entities",
        domain=D.UNCLASSIFIED,
        url=RUNTIME,
        cadence=ON_DEMAND,
        notes="Runtime state. 14 pts.",
        observed=4096,
    ),
    _d(
        "agent-vectors-index",
        "agent_vectors",
        cid="agent_vectors",
        domain=D.UNCLASSIFIED,
        url=RUNTIME,
        cadence=ON_DEMAND,
        notes="Agent-registry routing index. 182 pts, twin empty.",
        observed=2560,
        retired=(),
    ),
    _d(
        "scanner-memory-state",
        "scanner_memory",
        cid="scanner_memory",
        domain=D.UNCLASSIFIED,
        url=RUNTIME,
        cadence=ON_DEMAND,
        notes="Scanner runtime state.",
        observed=2560,
        retired=(),
    ),
    _d(
        "mem0-migrations-state",
        "mem0migrations",
        cid="mem0migrations",
        domain=D.UNCLASSIFIED,
        url=RUNTIME,
        cadence=ON_DEMAND,
        notes="Migration bookkeeping.",
        observed=4096,
    ),
    _d(
        "does-not-exist-probe",
        "does_not_exist",
        cid="does_not_exist",
        domain=D.UNCLASSIFIED,
        url=PROBE,
        cadence=ON_DEMAND,
        notes="Deliberate 404 probe. 0 pts, 2560.",
        observed=2560,
        retired=(),
    ),
    _d(
        "tail-test-probe",
        "sp_tailtest",
        cid="sp_tailtest",
        domain=D.UNCLASSIFIED,
        url=PROBE,
        cadence=ON_DEMAND,
        notes="Sparse-payload tail test. 0 pts.",
        observed=4096,
    ),
)

__all__ = [
    "_NON_CORPUS",
]
