# Mutation Testing Baseline — rmi-rag

**Tool:** mutmut 3.6.0 · **Scope:** `src/rugbrain` (whole-package copy for import
integrity) · **Test selection:** `tests/test_guardrails.py`

## Status: first run IN PROGRESS (background)

- 72,146 mutants generated package-wide
- Uncovered modules skip instantly as "no tests" (expected — selection is
  guardrail tests only)
- **guardrails.py**: 304 covered mutants executing (~20–30 min)
- **confidence_scorer.py**: 198 mutants — ALL "no tests": **no dedicated test
  file exists yet** ← coverage gap, write tests before meaningful baseline

Record actual scores here when the run completes:
`python3 -c "import json,glob; ..."` over `mutants/**/*.meta`
(exit codes: 0=survived, 1=killed, 33=no-tests)

## Config (pyproject.toml)

```toml
[tool.mutmut]
source_paths = ["src/rugbrain"]
pytest_add_cli_args_test_selection = ["tests/test_guardrails.py", "--deselect", "tests/test_guardrails.py::test_mcp_bridge_guard_helpers_exist"]
```

The deselect works around a sandbox-ordering flake in a hasattr smoke test
(passes alone, fails after sibling tests under the sandbox).

## Fleet gotchas (same lessons as rmi-alpha MUTATION_BASELINE.md)

- Whole-package `source_paths` required: partial copies break sibling imports
  (`cannot import name 'mcp_bridge' from 'rugbrain' (unknown location)`)
- conftest must APPEND sys.path, never prepend
- No editable install of the package under test while running mutmut
