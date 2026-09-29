# DIM-DRIFT GATE — keeping the retired 2560 generation gone

**Status:** proposal, live and static halves both RED (correctly).
**Date:** 2026-09-29. **Owner of the check:** Lane C, `src/rugbrain/dim_drift.py`.

> "We are switching to 4096 qwen 8b embed. We will not be using local qwen 3 4b
> embed rerank anymore, so it needs elimination and switch over, not just
> ignoring the fact it's there." — operator, 2026-09-29

The requirement behind that sentence is *"never again a second of confusion on
this"*. A comment is not enforcement. This document is what the gate enforces
and, more importantly, **how it decides what is a violation**, because that
decision is the only genuinely hard part.

## Running it

```bash
python3 scripts/check_dim_drift.py              # both halves
python3 scripts/check_dim_drift.py --live       # Qdrant census only
python3 scripts/check_dim_drift.py --static     # source tree only
python3 scripts/check_dim_drift.py --json       # machine-readable
```

| Exit | Meaning |
|------|---------|
| `0` | both halves clean |
| `1` | drift found |
| `2` | Qdrant unreachable — **UNKNOWN**, deliberately distinct from clean |

Exit `2` exists because "cannot tell" must never render as "fine". A check that
reports green because the server was down has enforced nothing.

## Current verdict — both halves RED, and that is correct

**Live: RED.** 96 collections checked, **48 offenders** — 42 at 2560
(3,190,777 points) and 6 at 1024 (0 points). 48 are already canonical at 4096.
The bases are still present; this is exactly the state the gate exists to hold
visible until they are deleted. **The check has not been weakened to go green.**

**Static: RED.** 724 files scanned, **48 executable references** to the retired
generation across 34 files. The most important single finding:

```
scripts/embed_lane.py:48   # Canonical dim — qwen3-embedding-4b native, same local and hosted.
scripts/embed_lane.py:49   EMBED_DIM = int(os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or "2560")
```

Line 44 of that file already sets the model to `qwen3-embedding-8b`. **The model
default says 8B and the dim default one line below says 2560.** That is the
defect the operator asked to make impossible, still present, and this gate
catches it.

## Why a static check cannot be a grep

100+ files in this tree mention 2560. Almost all are prose describing a dead
generation. The two failure modes are symmetric and both are real:

* Flag every `2560` → fires forever on correct code → gets muted → worse than
  no check.
* Ignore every `2560` → misses `embed_lane.py:49` above → the exact bug returns.

So the scanner **parses** (`ast`) and sorts every hit into one of four classes.

| Class | Verdict | Example |
|-------|---------|---------|
| `REGISTRY_DECLARATION` | never a failure | `lane_table.PRESERVED_LANE_DIM`, `lane_resolver.RETIRED_LANE_DIM`, the `MODEL_DIMS` key |
| `MANIFEST_RECORD` | never a failure | corpus manifest `observed=2560`, `retired=(2560,)` |
| `EXECUTABLE_DEFAULT` | **FAILURE** | a `getenv` fallback, a dim table entry, a `VectorParams(size=...)` |
| `MISLEADING_PROSE` | reported, not a failure | a docstring claiming the retired generation is "canonical" |

**No rule is keyed to a filename.** Exempting `dim_policy.py` by path would be
the exemption that rots — the next registry file would be flagged, and someone
would add a second exemption. Every exemption is structural:

* a **dict key** naming a model is a declaration (this is the `MODEL_DIMS` shape);
* a **dict value whose sibling keys are model names** is a declaration entry;
* a binding whose **name contains** `retired`/`preserved`/`legacy`/`deprecated`
  is a declaration (`PRESERVED_LANE_DIM`);
* a **manifest field** (`observed`, `retired`, `dim`) is a record;
* an `os.getenv` fallback **or an `or`-chain fallback** is a default;
* a literal **inside arithmetic** (`5 * 1024 * 1024`) is a byte size, not a
  vector width — this is what stops four backup-path false positives;
* a value bound to a name **consumed by `VectorParams(size=...)`** is a width,
  which is how `size = vector_size or 2560` is caught despite the `size` name.

Everything else is a default. **The retired set is derived from the registry**
(`RETIRED_LANE_DIM`, `PRESERVED_LANE_DIM`, `PINNED_DIMS`), so eliminating a
generation from the registry eliminates it from the gate. Nothing is hardcoded.

## False PROSE is reported separately, on purpose

A stale comment breaks nothing at runtime, so it does not fail the gate. But a
**docstring that asserts something untrue is contract**, and it misleads the
next reader into reinstating exactly the default this gate forbids. 115 prose
claims are currently reported, including `embed_lane.py:48` ("Canonical dim —
qwen3-embedding-4b native") sitting directly above the bug.

## The Qdrant shape that caused the earlier false alarm

`config.params.vectors` is **either**:

```jsonc
{"size": 4096, "distance": "Cosine"}                       // bare (90 of 96 here)
{"dense": {"size": 4096, "distance": "Cosine"}}           // named (6 of 96 here)
```

Both occur at 4096 on this host. **An earlier attempt at this check read only
one shape and reported healthy, populated 4096 collections as having "no vector
config."** The gate handles both explicitly, reuses the repo's own
`dense_vector_dimensions` parser so it cannot disagree with the write path, and
`test_live_half_FAILS_on_a_named_vector_2560_collection` pins it.

## Mutation evidence — the checks have been seen red

A check that has only ever been green proves nothing. Four deliberate breakages,
each verified to turn the suite red and then restored:

| Mutation | Result |
|----------|--------|
| Static half returns no failures | **6 failed**, 27 passed |
| Live half never flags an offender | **5 failed**, 28 passed |
| Live half reads only the bare shape (the original bug) | **4 failed**, 29 passed |
| Everything classified as a declaration (the "useless check" failure) | **4 failed**, 29 passed |

Plus in-suite inject/remove cycles: a synthetic 2560 collection and a 1024
collection against a throwaway local HTTP server, and injected 4B/2560/or-chain/
vector-schema defaults written to `tmp_path` and deleted again. **Qdrant is
never written to; the live tests use an ephemeral `http.server` on port 0.**

## Where it should run continuously

**`systemd` timer, hourly** — `systemd/rugbrain-dim-drift.service` +
`.timer`, proposed and **not installed**.

Why a timer rather than CI: **fleet CI here has historically had zero successful
runs**, so a CI job is a control that never executes. A timer on the host that
owns the corpus is the control that will actually run. Why hourly: the failure
is silent and cumulative, the run is ~5s and `MemoryMax=512M`, and an hour
catches a bad default at commit time. `Persistent=true` so a reboot re-runs it.
`MemoryMax` only, never `MemoryHigh` — a throttle would make the gate slow
instead of bounded.

```bash
cp systemd/rugbrain-dim-drift.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now rugbrain-dim-drift.timer
systemctl list-timers rugbrain-dim-drift.timer
```

## What is deliberately NOT done

* **The 48 live offenders are not fixed here.** They are owned by the elimination
  work (Lane A's `scripts/eliminate_4b.py`). This gate reports them.
* **The 48 static failures are not fixed here.** Every one of them is in a file
  another lane owns, or is a source file this lane may not edit. They are
  enumerated by `python3 scripts/check_dim_drift.py --static`.
* **Nothing was installed.** Units are proposed only.
