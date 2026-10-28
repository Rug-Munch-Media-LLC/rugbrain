# Content value, dedupe and routing — one decided path

Owner files, one job each. Nothing here publishes: routing returns a decision a
publisher acts on.

| Stage | File | Job |
|---|---|---|
| value | `src/rugbrain/content_value.py` + `content_value_components.py` | explainable 0..1 score with a stored breakdown |
| dedupe | `src/rugbrain/content_dedupe.py` + `content_dedupe_layers.py` | exact / near / semantic collapse, with a report |
| history | `src/rugbrain/content_history.py` | cross-day repeats, per-channel per-day counts |
| routing | `src/rugbrain/content_routing.py` | tier → channel, daily caps, gate hand-off |
| ordering | `src/rugbrain/content_rank_order.py` | 0.6B rerank lane, order only (no floor) |
| glue | `src/rugbrain/content_cream.py` | dedupe → score → route → order, lanes injected |

```
pool → tri-dedupe → value score → content_gate.decide → tier + daily cap → rerank order
```

## 1. Value score

`value = Σ(component × weight) − dedupe_penalty × 0.35`, all components 0..1.

| Component | Weight | Unit |
|---|---|---|
| novelty | 0.22 | `1 − strongest similarity to anything already published` |
| source diversity | 0.14 | `log2(distinct sources) / 2` — 1 source 0.00, 4+ 1.00 |
| on-chain magnitude | 0.18 | `log10(1 + USD) / 10` — $1M 0.60, $1B 0.90; whale-only 0.35 |
| recency | 0.16 | `0.5 ** (age_h / 12)` — 12h 0.50; undated 0.00 |
| citation strength | 0.12 | 0.4·link + 0.4·corroboration + 0.2·numeric claims |
| reader-interest prior | 0.18 | category prior + severity bonus (critical +0.10) |
| dedupe penalty | −0.35 | strongest layer evidence / that layer's own threshold |

The breakdown is stored with the score (`components`, each with `raw`, `weight`,
`contribution`), so a `0.52` can be argued with line by line.

## 2. Tri-dedupe

Layers run cheapest first; a layer only runs when the cheaper ones miss.

| Layer | Test | Bar | What it catches |
|---|---|---|---|
| exact | SHA-256 of normalised title+body | equal | copy-paste, emoji/`@mention`/URL/case differences |
| near | token-shingle Jaccard (size 3) | ≥ 0.62 | one edited sentence, dropped clause, retitled wire copy |
| semantic | cosine on corpus 8B/4096 (`:8004`) | ≥ 0.59 | cross-outlet paraphrase with no shared wording |

**Calibration (measured 2026-10-28, live lane):** same-story pairs min cosine
**0.6238**, different-story pairs max **0.5500**. The threshold is the midpoint
of that gap: ~0.04 margin on both sides. Recalibrate whenever the embedder or
the collection changes — the two numbers belong in the commit message.

Cross-day repeats and "same item twice in one day" are the ledger's job
(`content_history.SeenLedger`): same three layers against everything published
inside the retention window (14 days), plus per-channel/per-day counts. A repeat
is handed to the gate as `duplicate=True`, which routes it as an archive update,
never a second broadcast.

## 3. Routing table

| Tier | Threshold | Channel | Max/day | Why recorded |
|---|---|---|---|---|
| urgent | value ≥ 0.85 | `alerts` (+ `main` mirror) | 2 | big story: broadcast + mirror |
| lead | value ≥ 0.70 | `main` (+ site daily edition) | 3 | lead of the day |
| whales | value ≥ 0.62, category `onchain`, magnitude ≥ 0.50 | `whales` | 3 | screened for size |
| alpha | value ≥ 0.62, category `research`/`onchain`/`analysis` | `alpha` | 4 | depth beat |
| news | value ≥ 0.55 | `news` | 6 | solid news value, tighter format |
| site | value ≥ 0.35 | `site` | 25 | daily edition / archive |

Precedence, not severity: the FIRST tier an item clears claims it, so one story
never occupies two channels on the same day. A full tier falls THROUGH to the
next matching tier, and the pass-over is recorded (`urgent full (2/2)`). The
gate still wins — `skip`, `feed` (below the broadcast bar, unknown category, or a
duplicate) and `both` are `content_gate.decide`'s answers, and routing never
re-derives them (`test_routing_calls_the_gate_rather_than_reimplementing_it`).

## 4. Ordering

`content_rank_order.order_by_rerank` sequences the picks that earned a
broadcast slot on the **0.6B rerank lane (`:8030`)**, the deployed lane, not a
new model. It applies **no relevance floor**: measured on the fixture set, that
head returns logit-like scores (best 0.0051, median 0.0001) while the 8B lane
returns probabilities (on-intent 0.22) — the 8B's `MIN_RELEVANCE = 0.15` would
drop the whole pool here. The lane orders; it never admits. Failure degrades to
value order, tagged `rerank_method="none"`.

## 5. Measured on the fixtures

12 items, 6 true stories, `tests/fixtures/content/value_fixtures.json`
(live embed `:8004`, live rerank `:8030`, 2026-10-28):

```
suppressed pairs true=6 false=0 missed=0
precision=1.000 recall=1.000
stories kept: 6 of 6
layers used: exact (kelp-tg→kelp-alt, mango-tg→mango-rss),
             near  (kelp-day2→kelp-alt, sol-outage-stale→sol-outage),
             semantic (mango-x→mango-rss cos 0.6187, whale-x→whale-move cos 0.8912)
```

The semantic floor alone, swept over the same fixtures: thresholds 0.58–0.62
give precision 1.000 / recall 1.000; 0.63 drops the cross-outlet paraphrase
(recall 0.875); 0.55 admits one false merge (precision 0.889).

Reproduce: `python3 scripts/eval_content_value.py` (needs the Talos lanes).

## 6. Known drift (not fixed here)

`QWEN_EMBED_URL` still defaults to `:8024`, which now serves the 0.6B/**1024**
supermemory lane; the width guard refuses those vectors, so
`rugbrain.embeddings` reports the chain exhausted from any process without the
compose override. Live: `:8004` = 8B/**4096** (corpus), `:8024` = 0.6B/1024.
