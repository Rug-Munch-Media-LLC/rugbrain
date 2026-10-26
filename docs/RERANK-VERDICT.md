# Rerank verdict — where Qwen3-Reranker-8B earns its latency

Status: decided 2026-10-27. Applies to the `rerank` flag on `/api/v1/search`,
`/api/v1/search/multi`, and every `rugbrain_*` MCP tool that exposes it.

## Measurement

Harness: `scripts/eval_editorial_rerank.py` over the editorial golden set
(`eval/golden/editorial_news_golden.jsonl`), n = 10 judged queries.

| metric | no rerank | rerank (8B) |
| --- | --- | --- |
| ΔMRR | — | **+0.008** |
| latency | baseline | **~10x** the un-reranked call |

+0.008 MRR is inside the noise of a 10-query set: it is not a quality claim, it
is an absence of one. Paying 10x latency for a number that small is a bad trade
wherever the latency is user-visible.

## Verdict

**Rerank is not load-bearing for editorial/news retrieval.** Off by default in
the latency-sensitive MCP surface (`rugbrain_market_brief`,
`rugbrain_social_sentiment`, `rugbrain_news`). Leaving it on was not neutral:
three sequential reranked legs in `rugbrain_market_brief` outlived the 30 s
client timeout and surfaced as `MCP server process failed`.

**Where it is load-bearing.** Queries whose *identifier* is short and whose
documents are long — contract/audit lookups (`contract_audits_8b`,
`crypto_vulnerabilities_8b`), and scam-pattern queries where the lexical match
is weak. There the first-stage vector score ranks generic documents above the
specific finding, and rerank is the only stage that reads the whole document.
Keep it on for those collections, and for `rugbrain_scam_radar`.

## Rules

1. A tool exposed to an interactive agent must not spend 10x latency on a
   +0.008 MRR effect. Default `rerank=False` on the news/social/brief surface.
2. Do not re-litigate this with a smaller harness. Any change needs a judged
   set large enough that ±0.008 is outside the interval, plus a latency budget.
3. `rerank=True` remains available per call — callers who need it can pay for it.
