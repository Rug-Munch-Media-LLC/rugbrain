"""Quality classification for docs sets — measured by sampling URLs, never guessed."""
import re
from urllib.parse import urlparse

LANDING = {"index", "home", "landing", "overview", "main", "readme", "pricing",
           "careers", "contact", "sitemap", "blog", "news", "changelog", "releases"}
REFERENCE = ("/api", "reference", "rpc", "cli", "sdk", "config", "parameter",
             "types", "errors", "spec", "endpoint")
GUIDE = ("guide", "tutorial", "quickstart", "how-to", "howto", "learn",
         "getting-started", "concept", "example", "walkthrough")


def _slug(path):
    segs = [s for s in path.split("/") if s]
    last = segs[-1].lower() if segs else ""
    return segs, re.sub(r"\.(html?|md|php)$", "", last)


def classify_url(url):
    """Classify one docs page: landing | reference | guide | mixed."""
    path = urlparse(url or "").path or "/"
    segs, last = _slug(path)
    if not segs or last in LANDING:
        return "landing"
    low = path.lower()
    if any(k in low for k in REFERENCE):
        return "reference"
    if any(k in low for k in GUIDE):
        return "guide"
    return "mixed"


def summarise(paths):
    """Aggregate sampled paths into a per-set quality verdict."""
    cats = [classify_url(p) for p in paths] or ["mixed"]
    landing = cats.count("landing") / len(cats)
    body = [c for c in cats if c != "landing"] or ["mixed"]
    counts = {c: body.count(c) for c in set(body)}
    top = max(counts, key=counts.get)
    if landing >= 0.5:
        quality = "landing"
    elif landing >= 0.2:
        quality = "mixed"
    else:
        quality = top
    return {"quality": quality, "landing_ratio": round(landing, 2),
            "sampled_pages": len(cats)}


def validate(entry):
    """Return a list of gate violations for one registry entry."""
    errs = []
    for field in ("library_id", "display_name", "source_repo", "source_url",
                  "licence", "fetched_at", "chunk_count"):
        if not entry.get(field):
            errs.append("missing " + field)
    if str(entry.get("licence", "")).upper() in ("UNKNOWN", "NOASSERTION"):
        errs.append("licence unknown")
    if entry.get("quality") == "landing":
        errs.append("landing-pages-only (debt)")
    elif float(entry.get("landing_ratio") or 0) >= 0.4:
        errs.append("landing_ratio %.2f >= 0.40" % entry["landing_ratio"])
    return errs
