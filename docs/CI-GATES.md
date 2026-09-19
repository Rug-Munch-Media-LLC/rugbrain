# CI Gates — what runs, how it fails, how to ratchet down

This document is the contract for RugBrain's blocking CI gates. Every gate
below runs on **push to `main` and on every pull request** (`.forgejo/workflows/ci.yml`)
and **fails the job** on violation — none of them carry `|| true`.

## Blocking gates in the `quality` job

| Order | Step | Command | Fails when |
|------:|------|---------|-----------|
| 1 | Ruff | `ruff check src/ tests/` | any lint/format error |
| 2 | Mypy | `mypy src/rugbrain/ --ignore-missing-imports` | any type error |
| 3 | Import boundaries | `lint-imports --config .importlinter` | forbidden import contract broken |
| 4 | Structure ratchet | `python3 scripts/check-structure.py --repo .` | see below |
| 5 | Monkeypatch ratchet | `python3 scripts/check-monkeypatch.py --repo .` | count > committed baseline |
| 6 | Gate self-test | `bash scripts/ci-gate-selftest.sh` | any gate stops catching its violation |
| 7 | Eval smoke | `bash scripts/run-eval-smoke.sh` (if present) | recall@10 / MRR / nDCG below baseline |
| 8 | Tests + coverage | `pytest ... --cov-fail-under=60` | any test fails, coverage < 60% |
| 9 | Per-diff coverage | `diff-cover coverage.xml --compare-branch=origin/main --fail-under=60` | changed lines < 60% covered |

Eval smoke is the only conditionally-skipped step: it runs when
`scripts/run-eval-smoke.sh` exists (it does), and *fail-opens* only when Qdrant
is unreachable (`EVAL_STRICT=1` turns that skip into a failure).

## Structure policy — firm ≤200 lines

`scripts/check-structure.py` enforces a one-way size ratchet over `src/rugbrain/**/*.py`:

| Condition | Outcome |
|-----------|---------|
| file **> 150** lines | `[report]` advisory only |
| file **> 200** lines, **not** in `scripts/structure-baseline.json` | **FAIL** (`NEW file >200`) |
| file **> 500** lines, not allowlisted | **FAIL** (`NEW file >500`) |
| allowlisted file **grows** past its recorded count | **FAIL** (`allowlisted file grew`) |
| allowlisted file now **≤ 200** or gone | `[remove]` — take it out of the allowlist |
| allowlisted file shrank | `[shrink]` — run `--update-baseline` to lock the win |

The baseline (`scripts/structure-baseline.json`) was regenerated once on
2026-09-19 to grandfather the then-current 111 files > 200 lines (the previous
allowlist had 112; `search/engine/postprocess.py` sat at exactly 200 and is now
advisory). **From that cut-over forward the allowlist may only shrink.**

### Ratcheting further down

1. Split an allowlisted file so it drops ≤ 200 lines.
2. Run `python3 scripts/check-structure.py --repo .` — it prints `[remove]`.
3. Delete that entry from `scripts/structure-baseline.json`.
4. For files that shrank but are still > 200, run
   `python3 scripts/check-structure.py --repo . --update-baseline` to lower the
   recorded count (it refuses to raise any entry or to add a new > 200 file).

### `--update-baseline` safety

- Refuses to raise an existing recorded count (`shrink-only`).
- Refuses to grandfather a **new** > 200-line file (bootstrap excepted).
- Rewrites only files > 200; anything smaller leaves the allowlist.

## Monkeypatch policy — decrease-only

`scripts/check-monkeypatch.py` counts monkeypatch-attribute access, `mock.patch`
calls, `@patch(...)` decorators, `patch.object` calls and `del sys.modules`
across the repo (Python sources only). The repo-wide total may never exceed
`scripts/monkeypatch-baseline.json["total"]`. Fix the root cause; never add a
new patch to buy time. `--update-baseline` accepts decreases and refuses raises.

## Gate self-test — proving the gates bite

`scripts/ci-gate-selftest.sh` builds a throwaway fixture repo and injects one
violation at a time. Each injection must make its gate exit 1; a clean fixture
must exit 0. Any mismatch exits non-zero and fails CI. This prevents a gate from
silently becoming a no-op.

Assertions covered: 501-line file fails; 201-line non-allowlisted file fails;
allowlist growth fails; +1 monkeypatch fails; clean fixture passes.

## Import boundaries

`.importlinter` declares thin-router, route-isolation, `_legacy` quarantine and
no-`app.*`/no-`rugmunchbot` contracts. Known debt is explicitly listed with an
`ignore_imports` entry — never blanket-excluded.

## Local reproduction

```bash
.venv/bin/python -m pytest tests/test_structure_gate.py tests/test_monkeypatch_gate.py \
  tests/test_import_boundaries.py -q
bash scripts/ci-gate-selftest.sh
python3 scripts/check-structure.py --repo .
python3 scripts/check-monkeypatch.py --repo .
```
