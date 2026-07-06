<!--
SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Part of RMI RAG — https://git.rugmunch.io/RugMunchMedia/rmi-rag
-->

# Contributing to RMI RAG

> Qdrant collections, embeddings, ingestion scripts. Python 3.12.

## TL;DR

```bash
git checkout main && git pull --rebase origin main
git checkout -b feat/my-change
# …make changes…
make lint test            # pre-push gate
make commit               # conventional commits via commitizen
git push origin feat/my-change
# Open a PR on Forgejo (git.rugmunch.io/RugMunchMedia/rmi-rag)
```

## Repository Model

Single-tenant branch-PR pattern. `feat/*` branches merged to `main` after CI passes.

### Branches

| Branch             | Purpose                                          |
|--------------------|--------------------------------------------------|
| `main`             | Production-ready. Protected. Forgejo-mirrored.   |
| `feat/<scope>`     | New feature or capability.                       |
| `fix/<scope>`      | Bug fix.                                         |
| `chore/<scope>`    | Tooling, deps, formatting, non-functional.       |
| `refactor/<scope>` | Code restructure, no behavior change.           |
| `docs/<scope>`     | Documentation only.                              |

### Commit Convention

Conventional Commits.

```bash
feat(collections): add wallet_profiles_v2
fix(ingest): handle empty Ponder response
docs: update ARCHITECTURE.md
chore(deps): bump qdrant-client to 1.10
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `style`, `test`, `chore`,
`ops`, `security`, `ci`, `build`, `perf`, `revert`.

## Remotes

| Remote      | URL                                                       | Role                          |
|-------------|-----------------------------------------------------------|-------------------------------|
| `origin`    | `ssh://git@git.rugmunch.io:2222/RugMunchMedia/rmi-rag.git`| Canonical — Forgejo.          |

## Pre-Push Checklist

Before pushing any branch:

```bash
# If Makefile exists:
make lint test
# Else at minimum:
ruff check scripts/ collections/
python3 -m py_compile scripts/*.py
```

CI is defined in `.forgejo/workflows/ci.yml`.

## License

By contributing, you agree your work is licensed under the MIT License.

## Further Reading

- `AGENTS.md` — repo contract, owners, commands
- `ARCHITECTURE.md` — module layout, Qdrant collections
- `PRODUCT.md` — scope, anti-scope, success criteria
- `../standards/CONVENTIONS.md` — fleet-wide conventions