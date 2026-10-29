"""Part 4/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F403
from _parts_ingest_scam_knowledge.p1 import (
    _chunk_text,
    _date_to_ts,
    _detect_attack_vector,
    _detect_chain,
    _normalize_date,
    _parse_amount_lost,
)
from _parts_ingest_scam_knowledge.p3 import _extract_description, _parse_defihacklabs_incidents


def _defihacklabs_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Build one doc per PoC chunk, merging incident metadata in.

    Files under ~4KB are kept whole; larger PoCs are split into overlapping
    windows so each chunk is individually searchable (a 300-line PoC would
    otherwise exceed embedding context limits).
    """
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return []
    files = sorted(src_dir.rglob("*.sol"))
    files = [f for f in files if f.name not in _SKIP_SOL]
    if limit:
        files = files[:limit]
    incidents = _parse_defihacklabs_incidents(repo_root)
    docs: list[dict[str, Any]] = []
    for f in files:
        try:
            code = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not code.strip():
            continue
        rel_path = f.relative_to(repo_root).as_posix()
        contract_match = _CONTRACT_RE.search(code)
        contract_name = contract_match.group(1) if contract_match else ""
        incident = (
            incidents.get(re.sub(r"[^a-z0-9]+", "", contract_name.lower()))
            if contract_name
            else None
        )
        attack = incident.get("attack_type", "") if incident else ""
        vector = _detect_attack_vector(f"{rel_path} {attack} {code[:2000]}")
        chain = _detect_chain(f"{rel_path} {attack}")
        amount = _parse_amount_lost(incident.get("amount")) if incident else None
        title = f"DeFi exploit PoC: {contract_name or f.name}"
        description = _extract_description(code)
        base_metadata: dict[str, Any] = {
            "source_type": "defihacklabs",
            "protocol": (incident or {}).get("protocol", "") or contract_name,
            "contract_name": contract_name,
            "date": _normalize_date((incident or {}).get("date")),
            "attack_vector": vector,
            "chain": chain or "EVM",
            "amount_lost_usd": amount,
            "severity": "critical" if amount and amount >= 1e7 else "high",
            "source_url": f"https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/{rel_path}",
            "rel_path": rel_path,
        }
        if incident:
            base_metadata["attack_description"] = attack
        chunks = _chunk_text(code)
        for ci, chunk in enumerate(chunks):
            chunk_key = f"{rel_path}:{ci}" if len(chunks) > 1 else rel_path
            meta = dict(base_metadata)
            if description:
                meta["description"] = description[:500]
            docs.append(
                {
                    "rel_key": f"defihacklabs:{chunk_key}",
                    "title": title,
                    "text": chunk,
                    "metadata": meta,
                }
            )
    return docs


async def _fetch_rekt_leaderboard() -> list[dict[str, Any]]:
    """Fetch the rekt.news leaderboard and extract its __NEXT_DATA__ JSON."""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
        r = await c.get(REKT_LEADERBOARD_URL, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    html = r.text
    idx = html.find("__NEXT_DATA__")
    if idx < 0:
        raise RuntimeError("rekt.news leaderboard: __NEXT_DATA__ not found")
    start = html.find(">", idx) + 1
    end = html.find("</script>", start)
    data = json.loads(html[start:end])
    leaderboard = data.get("props", {}).get("pageProps", {}).get("leaderboard", [])
    return leaderboard or []


def _rekt_docs(entries: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    if limit:
        entries = entries[:limit]
    docs: list[dict[str, Any]] = []
    for entry in entries:
        slug = str(entry.get("slug", ""))
        title = str(entry.get("title", "")).strip()
        excerpt = str(entry.get("excerpt", "")).strip()
        rekt = entry.get("rekt", {}) or {}
        amount = _parse_amount_lost(rekt.get("amount")) if rekt.get("amount") is not None else None
        date_raw = rekt.get("date") or entry.get("date")
        date_iso = _normalize_date(date_raw)
        tags = entry.get("tags", []) or []
        tags_text = ", ".join(str(t) for t in tags)
        vector = _detect_attack_vector(f"{title} {tags_text} {excerpt}")
        chain = _detect_chain(f"{title} {tags_text} {excerpt}")
        text = (
            f"Title: {title}\n"
            f"Date: {date_iso}\n"
            f"Funds lost: {amount if amount is not None else 'unknown'}\n"
            f"Tags: {tags_text}\n"
            f"Excerpt: {excerpt}"
        )
        docs.append(
            {
                "rel_key": f"rekt_news:{slug}",
                "title": title,
                "text": text,
                "metadata": {
                    "source_type": "rekt_news",
                    "protocol": title,
                    "date": date_iso,
                    "date_ts": _date_to_ts(date_iso),
                    "attack_vector": vector,
                    "chain": chain,
                    "amount_lost_usd": amount,
                    "severity": "critical"
                    if amount and amount >= 1e7
                    else ("high" if amount else "medium"),
                    "source_url": f"https://rekt.news/{slug}/",
                    "slug": slug,
                },
            }
        )
    return docs


__all__ = [
    "_defihacklabs_docs",
    "_fetch_rekt_leaderboard",
    "_rekt_docs",
]
