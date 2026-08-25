# ⚠️ STALE DUPLICATE CLONE — DO NOT EDIT, DO NOT BUILD FROM HERE

This directory is a SECOND checkout of `RugMunchMedia/rugbrain` (same repo as
`/srv/work/repos/rmi-rag`, which is the PRODUCTION tree — docker compose builds
rugbrain-api from there).

History: commit 53cb130 (ruff CI fix) was stranded here, unpushed, 4+ commits
behind. It was rescued 2026-08-25 via cherry-pick → rmi-rag fb8f5cc (pushed to
all remotes). Nothing unique remains.

ACTION FOR OPERATOR: verify `git log origin/main..HEAD` is empty after a fetch,
then `rm -rf /srv/work/repos/rugbrain`.
