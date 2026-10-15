# systemd units — NOT the deploy canonical

> **Naming note:** `permanence` in every unit name below means the **nightly
> Qdrant→B2 backup job** — B2 is backup/DR, not a serving tier. The corpus is
> served from the server. See `docs/DATA-PLACEMENT.md`.

**The deploy canonical for fleet systemd units is `fleet-infra/systemd/`**
(flat layout, real paths, installed by `scripts/deploy/deploy-resource-control.sh`).

The units in this directory are **illustrative templates** and are not
deployable as-is:

- every path is a `/path/to/rugbrain/...` placeholder, and
- several describe a **different execution mechanism** than production runs.

## Drift found 2026-09-20

| template here | production actually runs |
|---|---|
| `rmi-rag-permanence.service` -> `scripts/b2-permanence-run.sh` | `rugbrain-permanence.service` -> `python3 scripts/b2_permanence.py` |
| `rmi-rag.service` -> `docker compose up rag-api` | `rugbrain-api.service` -> `scripts/run-rag-api.sh` |

The names diverged (`rmi-rag-*` here vs `rugbrain-*` deployed) **and** the
mechanism diverged (wrapper/docker-compose vs a direct script). Following these
templates would produce a unit that does not match production.

## What to do

- Read the real units in `fleet-infra/systemd/`: `rugbrain-permanence.{service,timer}`,
  `rugbrain-api.service`, `rmi-rag-tgweb.{service,timer}`, `rmi-rag-{eval-nightly,tg-ingest,x-ingest}.{service,timer}`,
  `rag-api.service`, `rag-embed-migration.service`.
- `rmi-rag-permanence.{service,timer}` here are stale duplicates of production's
  `rugbrain-permanence.*`; their `/etc` counterparts were archived on 2026-09-20.
- Do not deploy these directly. If this repo needs to ship units, generate them
  from fleet-infra rather than maintaining a second copy.
