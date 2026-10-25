"""Backtest: existing lexicon rule vs a causal cross-channel burst feature."""
from __future__ import annotations

import json
import sys
from collections import defaultdict

sys.path.insert(0, "src")
from rugbrain.ingest_sources.alt_ingest_shill import analyze  # noqa: E402

WINDOW = 24 * 3600
ROWS = [json.loads(l) for l in open("eval/shill/labelled_v1.jsonl", encoding="utf-8")]


def pr(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f1, 3)


def burst_scores(rows: list[dict]) -> dict[int, int]:
    """CAUSAL: for row i, count distinct channels sharing its dedupe_key with ts STRICTLY < t."""
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_key[r["dedupe_key"]].append(i)
    out: dict[int, int] = {}
    for idxs in by_key.values():
        seen: dict[str, int] = defaultdict(int)
        distinct = 0
        start = 0
        for pos, i in enumerate(idxs):
            while start < pos and rows[idxs[start]]["ts"] < rows[i]["ts"] - WINDOW:
                old = rows[idxs[start]]["channel_id"]
                seen[old] -= 1
                if seen[old] == 0:
                    distinct -= 1
                start += 1
            out[i] = distinct
            ch = rows[i]["channel_id"]
            seen[ch] += 1
            if seen[ch] == 1:
                distinct += 1
    return out


def evaluate(flags: list[bool], rows: list[dict], name: str) -> tuple:
    tp = sum(1 for f, r in zip(flags, rows) if f and r["label"] == "shill_campaign")
    fp = sum(1 for f, r in zip(flags, rows) if f and r["label"] == "organic")
    fn = sum(1 for f, r in zip(flags, rows) if not f and r["label"] == "shill_campaign")
    p, rec, f1 = pr(tp, fp, fn)
    print(f"{name:34s} P={p:<6} R={rec:<6} F1={f1:<6} tp={tp} fp={fp} fn={fn}")
    return p, rec, f1


def main() -> None:
    rows = sorted(ROWS, key=lambda r: r["ts"])
    pos = sum(r["label"] == "shill_campaign" for r in rows)
    print(f"n={len(rows)} pos={pos} neg={len(rows)-pos} "
          f"campaigns={len({r['dedupe_key'] for r in rows if r['label']=='shill_campaign'})}")
    lex = [analyze(r["text"], min_risk=2) is not None for r in rows]
    b9 = burst_scores(rows)
    print("-- threshold sweep, lexicon only (current rule, min_risk) --")
    for t in range(1, 9):
        evaluate([analyze(r["text"], min_risk=t) is not None for r in rows], rows, f"lexicon min_risk={t}")
    print("-- burst feature alone --")
    for t in (2, 3, 4):
        evaluate([b9[i] >= t for i in range(len(rows))], rows, f"burst>={t} distinct ch/24h")
    print("-- combined (one change: add causal burst) --")
    for t in (2, 3):
        flags = [(analyze(r["text"], min_risk=2) is not None) or b9[i] >= t for i, r in enumerate(rows)]
        evaluate(flags, rows, f"lexicon(2) OR burst>={t}")


if __name__ == "__main__":
    main()
