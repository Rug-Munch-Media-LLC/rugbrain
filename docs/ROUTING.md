# Routing layer — one documented door

`route_question(question)` (rugbrain-mcp) returns the layer, the exact tool and
arguments, and `degraded` + `use_instead` when the layer is unreachable (TCP
probe, cached 60 s). The table is data: edit `src/rugbrain/routing_table.json`,
no code change.

| Question shape | Layer | Server | Call |
|---|---|---|---|
| exact string / identifier / regex | zoekt_exact | zoekt | `search` |
| symbol, references, rename | serena_symbols | serena | `find_symbol` |
| how do WE build X, verdicts, gotchas | context_crypto_curated | context-crypto | `search_reference` |
| our repo architecture, call graph | metamcp_code | metamcp-code | `codebase_map` |
| fleet doctrine docs | docs_mcp | docs-mcp | `search_docs` |
| mainstream library API | context7 | context7 | `resolve-library-id` |
| public repo internals | deepwiki | deepwiki | `ask_wiki_question` |
| crypto news / scams / incidents | rugbrain_vector | rugbrain-mcp | `rugbrain_search` |

Argument names are resolved from the live schema by
`scripts/build-routing-table.py`; `scripts/capture-layer-inventory.py` snapshots
the inventory to `tests/fixtures/layer_inventory.json`, and the test suite fails
if the table names a tool or argument a server does not serve.
