"""Part 5/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p1 import _detect_attack_vector, _detect_chain

def _parse_yaml_entries(text: str, source_file: str, source_url: str) -> list[dict[str, Any]]:
    """Lightweight YAML parse — the blacklist files are simple list-of-dicts.

    We avoid a hard PyYAML dependency in prod paths; if PyYAML is available it
    is used as a more robust fallback (see _cryptoscamdb_docs).
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in text.splitlines():
        m = _YAML_KEY_RE.match(line)
        if m:
            if current:
                entries.append(current)
            current = {"_file": source_file, "_url": source_url, "name": m.group(1).strip()}
            continue
        if not current:
            continue
        m = _YAML_URL_RE.match(line)
        if m:
            current["url"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_CAT_RE.match(line)
        if m:
            current["category"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_SUB_RE.match(line)
        if m:
            current["subcategory"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_DESC_RE.match(line)
        if m:
            current["description"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_ADDR_RE.match(line)
        if m:
            current.setdefault("addresses", []).append(m.group(1))
            continue
    if current:
        entries.append(current)
    return entries


def _cryptoscamdb_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Parse urls.yaml + uris.yaml (scam URLs/accounts) into pattern docs."""
    docs: list[dict[str, Any]] = []
    data_dir = repo_root / "data"
    if not data_dir.exists():
        return []
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is None:
        logger.info("PyYAML unavailable — using lightweight YAML parser for cryptoscamdb")
    for filename in ("urls.yaml", "uris.yaml"):
        path = data_dir / filename
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        entries: list[dict[str, Any]] = []
        if yaml is not None:
            try:
                parsed = yaml.safe_load(raw)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            addr: list[str] = []
                            for _chain_name, addrs in (item.get("addresses") or {}).items():
                                if isinstance(addrs, list):
                                    addr.extend(str(a) for a in addrs)
                            entries.append({**item, "_addresses": addr})
            except Exception:
                entries = []
        if not entries:
            entries = _parse_yaml_entries(
                raw,
                filename,
                f"https://github.com/CryptoScamDB/blacklist/blob/master/data/{filename}",
            )
        if limit:
            entries = entries[:limit]
        for entry in entries:
            name = str(entry.get("name", ""))
            url = str(entry.get("url", ""))
            category = str(entry.get("category", ""))
            subcategory = str(entry.get("subcategory", ""))
            description = str(entry.get("description", ""))
            addresses = entry.get("_addresses") or entry.get("addresses") or []
            vector = _detect_attack_vector(f"{name} {category} {subcategory} {description}")
            if "phish" in category.lower():
                vector = "phishing"
            chain = _detect_chain(f"{name} {subcategory} {description}")
            text = (
                f"Scam entry: {name}\n"
                f"URL: {url}\n"
                f"Category: {category} ({subcategory})\n"
                f"Description: {description}\n"
                f"Associated addresses: {', '.join(addresses[:10])}"
            )
            rel_key = f"cryptoscamdb:{name or url}"
            docs.append(
                {
                    "rel_key": rel_key,
                    "title": f"Crypto scam: {name or url}",
                    "text": text,
                    "metadata": {
                        "source_type": "cryptoscamdb",
                        "protocol": subcategory or name,
                        "scam_type": category,
                        "attack_vector": vector,
                        "chain": chain,
                        "severity": "high"
                        if category.lower() in ("scamming", "malware", "fake ico")
                        else "medium",
                        "source_url": f"https://github.com/CryptoScamDB/blacklist/blob/master/data/{filename}",
                        "addresses": addresses[:10],
                        "url": url,
                    },
                }
            )
    return docs


__all__ = [
    "_parse_yaml_entries",
    "_cryptoscamdb_docs",
]
