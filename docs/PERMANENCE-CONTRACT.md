# B2 permanence contract — daily vs weekly

Two contracts, because one budget cannot serve both. All numbers measured on
Talos 2026-10-23 from the journal and the B2 manifest.

## Daily (the nightly writer, `rugbrain-permanence.timer`)

* every Qdrant collection **except** those listed in
  `config/permanence-scope.json` (`daily_excluded`);
* budget: 4 firings x 30 min soft (`--max-seconds 1800`), 35 min slot;
* a day is complete when `landed >= expected` and `missing` is empty, where
  `landed` counts only entries that are **not** `partial`;
* the manifest stamps `daily_scope.excluded_from_daily` so a reader can tell
  "97 of 97 by design" from "97 of 98, one missing";
* `expected_shards` is pinned on every entry that names shards, on resume as
  well as on a fresh export, and `entry_is_done()` finishes a collection when
  `len(shards) >= expected_shards` — otherwise a collection the budget always
  stops mid-way could never be finished.

## Weekly (`rugbrain-permanence-elephant.timer`, Sunday 06/07/08/09)

`rag_documents_8b` only:

| metric | value |
| --- | --- |
| points | 1,136,363 |
| bytes full | ~28.3 GB (24.9 KB/point) |
| shard | 50,000 points = 1,243 MB, 5.1–9.7 min (median 8.3) |
| shards | 23 |
| export time | ~190 min |
| nightly budget | 120 min **for all 98 collections** |

190 min > 120 min, so it can never fit in the day it belongs to. Pinning
`expected_shards` makes it *countable*; only a dedicated window makes it
*finish*. Four 50-min firings resume from the last shard cursor and complete in
one Sunday.

Progress on 2026-10-23 before this contract: 650,000 / 1,136,363 points (57.2%),
11 of 23 shards, 16.95 GB in B2, `expected_shards` absent on 5 of 5 sharded
entries (0 could ever be judged finished).
