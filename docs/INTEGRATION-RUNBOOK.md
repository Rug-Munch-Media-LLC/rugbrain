# RugBrain — MCP + knowledge-graph integration runbook

<!-- SPDX-License-Identifier: MIT -->
<!-- Copyright (c) 2026 Rug Munch Media LLC -->

> **This document runs nothing.** It is a set of commands for a human who has
> decided to execute them. No service was restarted, no config edited, and no
> Qdrant collection written while producing it. Every "expected" line is output
> that was actually observed on this host on **2026-09-28**, not a prediction.
>
> Companion report: `/root/.knowledge/audit-reports/2026-09-28-integrations-report.md`

---

## Phase 0 — Preflight (read-only)

Run this whole block before anything else. If any "expected" line does not
match, **stop** — the later phases assume these facts.

```bash
# 1. Qdrant — MUST be 6334, not 6333.
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -i qdrant
#   expected: rmi-qdrant-v2  6334/tcp, [INTERNAL_HOST]:6334->6333/tcp, 127.0.0.1:6334->6333/tcp

curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6334/collections
#   expected: 200
#   Port 6333 answers nothing. That is not a fault to fix — do not
#   "repair" it by republishing 6333.

# 2. Neo4j — the ONLY container is mem0-neo4j. There is no rmi-neo4j.
docker ps --format '{{.Names}}' | grep -i neo4j
#   expected: mem0-neo4j
#   NEVER code against the name rmi-neo4j; it does not exist on this host.

# 3. metamcp namespaces — all four must answer 200.
KEY=$(cat /srv/metamcp/.fleet-agents-key)
for ns in code ops audit research; do
  printf '%s ' "$ns"
  curl -s -o /dev/null -w '%{http_code}\n' -X POST \
    "http://127.0.0.1:12008/metamcp/${ns}-api/mcp" \
    -H "Authorization: Bearer $KEY" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
done
#   expected: code 200 / ops 200 / audit 200 / research 200
#
#   NOTE: /api/namespaces returns a Next.js 404 HTML page. It is not the
#   API. The real shape is POST /metamcp/<slug>-api/mcp.
```

---

## Phase 1 — Register the RugBrain namespace on metamcp

MetaMCP is a **proxy**, not a host. The server must answer on its own port
before the namespace resolves, or you are debugging a proxy error that is
really a dead upstream.

### Step 1a — run the server as a unit

Do not run it bare in a login shell: it dies with the session and takes the
namespace down with it.

```bash
systemctl cat deploy/systemd/rugbrain-mcp.service   # review first
systemctl daemon-reload
systemctl enable --now rugbrain-mcp.service
systemctl status rugbrain-mcp.service --no-pager
#   expected: active (running)
```

### Step 1b — prove it speaks MCP *before* metamcp is in the path

`tools/list` is the cheapest possible call: no embeddings, no Qdrant write,
no side effects.

```bash
curl -s -X POST http://127.0.0.1:9099/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 -c 'import sys,json; [print(t["name"]) for t in json.load(sys.stdin)["result"]["tools"]]'
#   expected: exactly the tools listed in docs/MCP-SECURITY.md — no others
```

### Step 1c — add the namespace

Via the MetaMCP UI at `http://127.0.0.1:12008` (namespaces → add):

| field | value |
|---|---|
| slug | `rugbrain` |
| endpoint | `http://host.docker.internal:9099/mcp` |
| auth | none from metamcp; the *upstream* holds the keys |

If metamcp shares a compose network, `http://rugbrain-mcp:9099/mcp` works
instead. Do **not** enable the namespace until 1b has printed the tool list.

---

## Phase 2 — Key scoping

The RugBrain MCP reads Qdrant and writes Neo4j. **It needs no B2 key at all** —
that is a backup-verification surface and mixing the two would put a storage
key in an agent-reachable tool. (B2 is backup/DR only; the corpus is served
from the server — see [DATA-PLACEMENT.md](DATA-PLACEMENT.md).)

### Qdrant

Use a scoped API key limited to the four collections the digestion path
touches: `news_articles`, `known_scams`, `threat_intel_v2`, `scam_intel_v2`.

```bash
# Create (writes the key id to stdout ONCE — capture it, never commit it)
curl -X POST http://127.0.0.1:6334/collections/RUGBRAIN_RAG_KEY \
  -H 'Content-Type: application/json' -d '{"vectors":{},"payload":{}}'
```

Confirm the instance actually enforces collection-level ACLs before relying
on the scoping — an unconfigured instance returns a key that grants everything.

### Neo4j

**Do not** hand this server the full `neo4j` user. That user can read the
corpus-taxonomy nodes that share this instance. Create a scoped user:

```cypher
CREATE USER rugbrain_mcp SET PASSWORD '<from gopass>';
GRANT MATCH {group_id:'rugbrain'} ON GRAPH * NODES TO rugbrain_mcp;
```

The `group_id` predicate in the grant is the enforcement point: every node
this code writes carries `group_id='rugbrain'` (see
`src/rugbrain/digest/graph.py` → `_DEFAULT_GROUP`).

### Secrets

```bash
gopass insert rmi/rugbrain/mcp_qdrant_key
gopass insert rmi/rugbrain/mcp_neo4j_password
```

Secrets never go in a committed file. The systemd unit reads them from an
`EnvironmentFile` with mode `0600`.

---

## Phase 3 — Enable in the agent client

> Do not hand-edit `opencode.jsonc` while agents are running. It is 1MB+;
> a partial write leaves a broken config for every agent on the box.

```bash
# 1. Back up first.
cp -a ~/.config/opencode/opencode.jsonc \
      ~/.config/opencode/opencode.jsonc.bak.$(date +%s)
```

**2.** Add under `"mcp"`, matching the existing `metamcp-*` shape:

```jsonc
"metamcp-rugbrain": {
  "enabled": true,
  "type": "remote",
  "url": "http://127.0.0.1:12008/metamcp/rugbrain-api/mcp",
  "headers": {
    "Authorization": "Bearer {file:/srv/metamcp/.fleet-agents-key}"
  }
}
```

**3. Deny by default the tools you do not want in general agent context.**

In the `"tools"` object (which must come *after* the server block):

```jsonc
"metamcp-rugbrain_digest_document": false,
"metamcp-rugbrain_recall_memory": false
```

Ingest and memory-write are **operator** tools. Left enabled fleet-wide, any
agent can write into the crime corpus. See `docs/MCP-SECURITY.md` for the
full allowlist and the reasoning.

**4. Validate before restarting anything:**

```bash
python3 - <<'PY'
import json, re
s = open("/root/.config/opencode/opencode.jsonc").read()
s = re.sub(r"//.*", "", s)
s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
json.loads(s)
print("jsonc parses")
PY
#   expected: jsonc parses
#   A FAILURE here means do NOT restart.
```

**5. Restart opencode.** Tool lists are cached **per session** — an already
running agent will not see the new namespace until it is restarted. Verified
fleet behaviour, 2026-09-22.

---

## Phase 4 — Verification

Each step asserts one thing. Do not batch them; a batch reports one failure
and you re-run all of it.

```bash
KEY=$(cat /srv/metamcp/.fleet-agents-key)

# V1 — the namespace serves exactly the expected tool list.
curl -s -X POST http://127.0.0.1:12008/metamcp/rugbrain-api/mcp \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 -c 'import sys,json; ts=json.load(sys.stdin)["result"]["tools"]; print(len(ts)); [print(" -",t["name"]) for t in ts]'
#   expected: the documented count, and no unlisted tools

# V2 — a read tool returns real data through the gateway.
curl -s -X POST http://127.0.0.1:12008/metamcp/rugbrain-api/mcp \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
        "name":"scam_lookup",
        "arguments":{"address":"0xdAC17F958D2ee523a2206206994597C13D831ec7"}}}'
#   expected: JSON with a hits[] array.
#   An EMPTY array is a PASS (the address is simply not in the corpus).
#   A 500 is a fail.

# V3 — the write tool is NOT reachable from a general agent namespace.
#     Deliberately attempt the denied call. If it SUCCEEDS, the Phase 3
#     deny-list is not in effect and the surface is too wide.
#   expected: a permission error, not data

# V4 — point counts, before/after an ingest you explicitly initiated.
curl -s -X POST http://127.0.0.1:6334/collections/news_articles/points/count \
  -H 'Content-Type: application/json' -d '{"exact":true}'
#   expected: a count to compare
```

---

## Phase 5 — Rollback

Ordered least- to most-invasive. Stop at the first step that resolves the
symptom — each is independently reversible.

| # | Action | Reversible | Notes |
|---|---|---|---|
| **R1** | Disable the namespace in metamcp (set `enabled=false`) | instantly | No file touched. |
| **R2** | Remove the `metamcp-rugbrain` block from `opencode.jsonc`, restart | yes | Returns to fleet default: no RugBrain MCP. `cp -a` the Phase 3 backup back. |
| **R3** | `systemctl disable --now rugbrain-mcp.service` | yes | Config intact; agents with the namespace get a connection error rather than a wrong answer. |
| **R4** | Revoke keys | no | Do this if R1–R3 did not help, or immediately on suspected key leak. |

```bash
# R4 detail
gopass rm -f rmi/rugbrain/mcp_qdrant_key
gopass rm -f rmi/rugbrain/mcp_neo4j_password
docker exec mem0-neo4j cypher-shell -u neo4j -p "$PW" \
  "DROP USER rugbrain_mcp;"
```

### Data rollback — the part none of the above undoes

Digestion writes to **both** Qdrant and Neo4j. Removing a bad ingest requires
deleting points and episode nodes explicitly, by identity:

```bash
# Qdrant — by source
curl -X POST http://127.0.0.1:6334/collections/<name>/points/delete \
  -H 'Content-Type: application/json' \
  -d '{"filter":{"must":[{"key":"source","match":{"value":"<source>"}}]}}'

# Neo4j — episodes only. DETACH is required because MENTIONS edges point at
# entity nodes that other episodes still reference.
docker exec mem0-neo4j cypher-shell -u neo4j -p "$PW" \
  "MATCH (e:Episodic {group_id:'rugbrain', source:\$src}) DETACH DELETE e;"
```

Because edges carry `valid_at` rather than being hard-deleted, a rolled-back
ingest leaves the graph **consistent** — but verify with the query above
rather than assuming it. `Entity` nodes are intentionally *not* deleted: they
are shared across episodes, and a dangling entity is harmless while a deleted
one silently breaks other documents' links.

---

## Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `/api/namespaces` returns HTML | It is the Next.js UI, not the API | Use `POST /metamcp/<slug>-api/mcp` |
| Qdrant connection refused on 6333 | Wrong port; 6334 is correct | Use 6334 |
| `rmi-neo4j: not found` | That container does not exist | Use `mem0-neo4j` |
| Namespace added but `tools/list` empty | Upstream not reachable from the metamcp container | Test 1b; fix the upstream URL |
| New namespace invisible to a running agent | Tool lists cached per session | Restart opencode |
| Graph writes succeed, reads return nothing | Scoped user missing the `group_id` grant | Re-apply the Phase 2 Cypher grant |
