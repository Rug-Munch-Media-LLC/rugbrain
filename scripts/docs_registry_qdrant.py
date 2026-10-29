"""Thin Qdrant access used by the docs registry builder (facet-first, no full scan)."""
import json
import urllib.request

BASE = "http://127.0.0.1:6334"


def _call(path, payload=None, timeout=90):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.load(res)


def facets(collection, key="library_id", limit=2000):
    """Exact per-value point counts via the facet API (requires payload index)."""
    body = {"key": key, "limit": limit, "exact": True}
    hits = _call("/collections/%s/facet" % collection, body)["result"]["hits"]
    return {h["value"]: h["count"] for h in hits}


def scroll(collection, library_id, limit=40):
    body = {"limit": limit, "with_payload": True,
            "filter": {"must": [{"key": "library_id",
                                 "match": {"value": library_id}}]}}
    return [p["payload"] for p in
            _call("/collections/%s/points/scroll" % collection, body)["result"]["points"]]
