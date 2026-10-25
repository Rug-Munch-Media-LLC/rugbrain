"""Build a labelled shill set from the telegram message corpus (social_intel_8b).

Provenance: labels come from PROPAGATION STRUCTURE, not from the detector's
vocabulary. A message is POSITIVE when the identical normalised text appears in
>=3 distinct channels inside one 24h window (copy-paste coordination). Everything
else is NEGATIVE (organic chatter). This keeps the label independent of the rule
under test, so the detector cannot mark its own homework.
"""
from __future__ import annotations

import json
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass

QDRANT = "http://127.0.0.1:6334"
COLL = "social_intel_8b"
PREFIX = re.compile(r"^\[TG/[^\]]+\]\s*")
URL = re.compile(r"https?://\S+")
ADDR = re.compile(r"\b(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")
NONWORD = re.compile(r"[^a-z0-9 ]+")
SPACE = re.compile(r"\s+")
WINDOW = 24 * 3600
MIN_CHANNELS = 3


@dataclass(frozen=True)
class Msg:
    id: str
    channel: str
    ts: int
    text: str
    key: str


def _ts(raw: str) -> int:
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return 0


def dedupe_key(text: str) -> str:
    """Copy-paste fingerprint: drop channel prefix, URLs and addresses, keep words."""
    body = PREFIX.sub("", text or "").lower()
    body = ADDR.sub(" ", URL.sub(" ", body))
    words = SPACE.sub(" ", NONWORD.sub(" ", body)).strip().split()
    return " ".join(words[:40])


def scroll() -> list[dict]:
    out, off = [], None
    while True:
        body: dict = {"limit": 1000, "with_payload": True, "with_vector": False}
        if off is not None:
            body["offset"] = off
        req = urllib.request.Request(
            f"{QDRANT}/collections/{COLL}/points/scroll",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        page = json.load(urllib.request.urlopen(req, timeout=90))["result"]
        out.extend(page["points"])
        off = page.get("next_page_offset")
        if not off or not page["points"]:
            return out


def load() -> list[Msg]:
    msgs = []
    for p in scroll():
        payload = p.get("payload", {}) or {}
        meta = payload.get("metadata") or payload
        text = payload.get("content") or meta.get("content") or ""
        if meta.get("kind") != "telegram_message" or len(text) < 25:
            continue
        key = dedupe_key(text)
        if len(key.split()) < 4:
            continue
        msgs.append(Msg(str(p.get("id", payload.get("id", ""))), str(meta.get("channel_id", "")),
                        _ts(meta.get("ts", "")), text, key))
    msgs.sort(key=lambda m: m.ts)
    return msgs


def label(msgs: list[Msg]) -> dict[str, tuple[bool, int]]:
    """id -> (is_positive, distinct_channels_in_window). Causal: only past msgs used."""
    by_key: dict[str, list[Msg]] = defaultdict(list)
    for m in msgs:
        by_key[m.key].append(m)
    out: dict[str, tuple[bool, int]] = {}
    for group in by_key.values():
        seen: dict[str, int] = defaultdict(int)  # channel -> count in window
        distinct = 0
        start = 0
        for i, m in enumerate(group):
            seen[m.channel] += 1
            if seen[m.channel] == 1:
                distinct += 1
            while group[i].ts - group[start].ts > WINDOW:
                old = group[start]
                seen[old.channel] -= 1
                if seen[old.channel] == 0:
                    distinct -= 1
                start += 1
            out[m.id] = (distinct >= MIN_CHANNELS, distinct)
    return out


def main() -> None:
    msgs = load()
    labels = label(msgs)
    pos = [m for m in msgs if labels[m.id][0]]
    neg = [m for m in msgs if not labels[m.id][0]]
    print(f"messages={len(msgs)} positives={len(pos)} negatives={len(neg)}")
    print(f"positive campaigns(keys)={len({m.key for m in pos})} "
          f"channels={len({m.channel for m in msgs})}")
    with open("eval/shill/labelled_v1.jsonl", "w", encoding="utf-8") as fh:
        for m in msgs:
            p, ch = labels[m.id]
            fh.write(json.dumps({
                "id": m.id, "channel_id": m.channel, "ts": m.ts,
                "text": m.text, "dedupe_key": m.key,
                "label": "shill_campaign" if p else "organic",
                "label_provenance": f"cross_channel_dup:{ch}ch/24h",
            }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
