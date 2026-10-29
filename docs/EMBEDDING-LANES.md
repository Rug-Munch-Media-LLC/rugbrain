# Embedding lanes — canonical

Run `python3 scripts/embed_probe.py` before any ingest. It prints one JSON line per lane.

| Lane | Endpoint | Model | Dim | Key source | Notes |
|---|---|---|---|---|---|
| siliconflow-8b | `api.siliconflow.com/v1/embeddings` | `Qwen/Qwen3-Embedding-8B` | 4096 | `/srv/secrets/siliconflow_api_key` | fastest (~1.2 s), **file-first** |
| local-8b | `127.0.0.1:8024/v1/embeddings` | `qwen3-embedding-8b` | 4096 | `LLAMA_EMBED_KEY` in `/srv/models/embed-lane.env` | works but **~141 s/call cold** |
| local-0.6b | `127.0.0.1:8004` | Qwen3-Embedding-0.6B | 1024 | same file | **not corpus-valid** (supermemory lane only) |
| deepinfra | — | — | — | `DEEPINFRA_API_KEY` | **402 overdue invoices** — do not route |

Rules:
1. **File-first keys.** A stale env var must never shadow the secrets file — that made every lane report "provider dead" while a valid key sat on disk.
2. Corpus is 4096-d only. A 1024-d vector must never be written into a corpus collection.
3. Slow is not broken: the local 8B lane answers in ~141 s cold; a 20 s timeout reports a false 401/timeout.
4. `systemctl show -p Environment` does **not** show `EnvironmentFile` contents — read the file.
