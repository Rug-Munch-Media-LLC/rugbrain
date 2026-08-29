# ⚠️ DEPRECATED — CANONICAL REPO IS `rugbrain`

This checkout (`rmi-rag`) is **DEPRECATED** (2026-08-29). The canonical RAG
repo is `/srv/work/repos/rugbrain`, and the production `rag-api.service`
(uvicorn `rugbrain.main:app`, :8012) runs **from there**, not here.

**DO NOT run `rm -rf /srv/work/repos/rugbrain`.** An earlier version of this
file inverted the relationship (it called `rmi-rag` the PRODUCTION tree and
`rugbrain` the stale duplicate clone). That is **WRONG** and would delete the
live production tree. This checkout is the one being retired.

What to do here:
* Do **not** edit, build, deploy, or file issues in this checkout.
* Do **not** delete `/srv/work/repos/rugbrain`.
* **No source was lost** in the consolidation. The only two scripts this repo
  held that `rugbrain` lacked (`scripts/dual_tier/backfill_speed_vectors.py`
  and `scripts/reembed_deepinfra.py`) were ported into `rugbrain` on 2026-08-29.

## Archive steps — execute (for the build lead / operator; do NOT force, do NOT `rm -rf`)

1. **Confirm no running service references this checkout:**
   `systemctl cat rag-api.service` → `WorkingDirectory` and `ExecStart` must
   both be `/srv/work/repos/rugbrain` (already true, verified 2026-08-29).
   Grep all units for `rmi-rag` and repoint any that still reference it.
2. **Take a final backup of this tree** (history + all refs) so nothing is lost:
   `cd /srv/work/repos/rmi-rag && git fetch --all && git bundle create /var/backups/rmi-rag-archive.bundle --all`
3. **Create a PRIVATE archive namespace on forgejo**, e.g.
   `RugMunchMedia/archive-rmi-rag` (visibility = private).
4. **Push full history there (mirror):**
   `git remote add archive git@git.rugmunch.io:RugMunchMedia/archive-rmi-rag.git && git push --mirror archive`
5. **On the archive namespace:** set `private` visibility, tag `archived`,
   lock `main` (protected), and add a README banner pointing at `rugbrain`.
6. **Retire the public/mirror remotes for `rmi-rag`:** remove the radicle
   remote (P2P cannot be private — `git remote remove radicle`), and flip the
   codeberg/gitlab remotes to `private` or remove them so nothing public
   carries this history.
7. **Only after the archive namespace is confirmed intact:** mark this local
   checkout `.git` read-only (`chmod -R a-w .git`) to prevent further changes.
   Do **not** `rm -rf` the working tree unless the lead explicitly confirms the
   archive copy restore-tested cleanly.
