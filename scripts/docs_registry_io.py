"""Storage helpers for data/corpus/docs_registry.json."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "data", "corpus", "docs_registry.json")
FRESHNESS = os.path.join(ROOT, "data", "corpus", "docs_freshness.json")


def load(path=REGISTRY):
    if not os.path.exists(path):
        return {"entries": []}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("entries", [])
    return data


def save(data, path=REGISTRY):
    data["entries"] = sorted(data["entries"], key=lambda e: e["library_id"])
    data["count"] = len(data["entries"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return data


def load_freshness(path=FRESHNESS):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def find(entries, library_id):
    for entry in entries:
        if entry["library_id"] == library_id:
            return entry
    return None


def search(entries, query):
    q = query.lower()
    hits = []
    for entry in entries:
        hay = " ".join(str(entry.get(k, "")) for k in
                       ("library_id", "display_name", "source_repo", "source_url"))
        if q in hay.lower():
            hits.append(entry)
    return hits
