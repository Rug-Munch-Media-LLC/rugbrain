# scan_intel (2560) vs scan_intel_8b (4096) — divergence, producer, merge plan

Measured 2026-10-18 15:40–15:49 UTC against live Qdrant `rmi-qdrant-v2:6334`.

## Live state (not the doc numbers)

| Collection | Dim | Points | Addresses | scanned_at window |
|---|---|---|---|---|
| `scan_intel` (legacy) | 2560 | 10,271 → **10,308** | 10,270 | **2026-10-17 → 2026-10-18 only** |
| `scan_intel_8b` (canonical) | 4096 | 60,483 | 59,441 | 2026-09-23 → **2026-09-29 only** |

- Shared addresses: **17**. Legacy-only: **10,253**. Canonical-only: **59,424**.
- Neither is a superset. Nothing is deleted by this plan.

## PRODUCER (live defect — full root cause)

`rmi-backend/app/integrations/rag_helpers/scan_intel.py`

- `SCAN_COLLECTION = os.getenv("RMI_SCAN_INTEL_COLLECTION", "scan_intel")` — a **literal**
  collection name, so rugbrain's `_8b` lane resolver is bypassed entirely.
- `app/integrations/rag_helpers/embeddings.py`: `EMBED_MODEL_NAME="qwen3-embedding-4b"`,
  `EMBEDDING_DIM=2560`. Writes therefore cannot reach a 4096 collection.
- Called fire-and-forget from `app/domains/scanner/unified/persistence.py::persist_result`
  step 3b, inside the running `rmi-backend` container.
- **No systemd unit and no cron entry writes it.** `systemctl list-timers` and `crontab -l`
  contain no scan_intel producer. There is no service-layer switch to flip.

Proof it is live: 131 points with `scanned_at >= 15:30Z`; count moved 10,271 → 10,308
in ~8 minutes (+37).

Side effect of the same module: `ALERT_COLLECTION="threat_intel_v2"` and
`SHILL_COLLECTION="social_intel"` are also literal base names, written with the same
2560 embedder — same class of bypass for the alert/shill lanes.

## OVERLAP — 20 legacy-only addresses sampled

Even 20 sampled addresses (every 503rd of the 10,253): **0 found** in
`wallet_memory.entity_labels`, **0** in `wallet_memory.rag_scan_labels`, **0** in
`wallet_memory.scam_addresses`, and 0 in `scan_intel_8b` (by construction).
`rag_scan_labels` currently holds 52,411 rows and its `max(scanned_at)` is
**2026-09-29 08:57** — it mirrors the canonical lane and stopped when that lane did.
The Oct 17–18 scans exist **only** in the 2560 collection.

## BLOCKER — do not stop the writer yet

`app/models/scan.py` declares `__tablename__ = "scan_results"`, but that relation
**does not exist** in the `rmi` database (only `public.scan_reports` exists, a different
AI-report table). `persist_result` step 1 therefore raises `UndefinedTable`, is swallowed
("errors are logged but never raised"), and the Qdrant 2560 lane is currently the **only
durable copy** of these scans; the Redis copy has a 1-hour TTL.

Stopping the producer before that is fixed silently discards every scan from the stop
onward. This is why the lane was not switched off in this pass.

## MERGE PLAN

Payloads are sufficient for a **provable** reconstruction: every legacy point has
`address, chain, risk_level, risk_flags, safety_score, scanned_at` (0 missing on all
10,271). The canonical `_text` template is exactly:

```
Token scan on {chain}: {address} risk={risk_level}. Flags: {", ".join(risk_flags)}. Safety score {safety_score}.
```

Note: legacy point ids are Qdrant-assigned (`00000000-0001-…`), **not** `uuid5(doc_id)`,
so dedupe must be by `doc_id`, and merged points need fresh ids.

**Path A — re-embed into `scan_intel_8b` (recommended for search)**
1. Scroll `scan_intel` (payload only, 10,308 pts).
2. Rebuild `_text` from the template; set `_lane="Qwen/Qwen3-Embedding-8B"`.
3. Embed with Qwen3-Embedding-8B @ 4096 (DeepInfra), batch 96.
4. Upsert to `scan_intel_8b`, fresh uuid4 ids, 256/batch.
5. Verify: `count(filter doc_id in legacy set)` == 10,308; spot-check 20 searches.
6. Leave `scan_intel` in place (read-only) until the producer is repointed.

**Cost:** ~0.4M tokens of text (≈40 tokens/point) → **≈$0.004–0.01** on DeepInfra
Qwen3-Embedding-8B; **~5 minutes** embed + upsert, ~10 minutes with verification.
Storage delta: negligible (<50 MB).

**Path B — backfill ClickHouse only (durable mirror, no new vectors)**
`scripts/export_address_labels.py` already targets `wallet_memory.rag_scan_labels`
(52,411 rows, same schema, `source`/`provenance` columns). 10,308 rows → **seconds,
$0**. This preserves the data but does not make Oct 17–18 searchable in the 4096 lane.

**Recommended: A + B.** B first (cheap durability), then A (search), then repoint the
producer, then retire the 2560 base.

## Producer fix (after merge)

```python
# rag_helpers/scan_intel.py
SCAN_COLLECTION = os.getenv("RMI_SCAN_INTEL_COLLECTION", "scan_intel_8b")
# + a 4096 embedder for this path (EMBEDDING_DIM is 2560 and shared with
#   threat_intel_v2 / social_intel, so it must NOT be changed globally)
```
`embeddings.py` is shared with the alert and shill lanes; changing `EMBEDDING_DIM`
globally would break those writes. Add a lane-scoped embedder instead.

## Kill-switch (reversible) once `scan_results` exists

```python
if os.getenv("RMI_SCAN_INTEL_EMBED_ENABLED", "1") != "1":
    return
```
at the top of `embed_scan_result_bg`, set in the compose env, then
`docker compose up -d rmi-backend`. Revert = remove the env var.
