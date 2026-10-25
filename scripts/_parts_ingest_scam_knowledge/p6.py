"""Part 6/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F403
from _parts_ingest_scam_knowledge.p1 import (
    _chunk_markdown_sections,
    _date_to_ts,
    _detect_attack_vector,
    _detect_chain,
)


def _slowmist_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Parse SlowMist markdown knowledge (study notes, audit reports, translations)."""
    docs: list[dict[str, Any]] = []
    for subdir, url_tpl in _SLOWMIST_MD_FILES:
        base = repo_root / subdir
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not text.strip():
                continue
            rel = f.relative_to(repo_root).as_posix()
            rel_key = f"slowmist:{rel}"
            title = f.stem.replace("-", " ").replace("_", " ").strip()
            vector = _detect_attack_vector(text[:4000])
            chain = _detect_chain(f"{rel} {text[:2000]}")
            # Source-type: audit report vs study note
            if "audit report" in text.lower()[:400] or "audit" in rel.lower():
                source_type = "audit_report"
            elif "translation" in rel:
                source_type = "translation"
            else:
                source_type = "study_note"
            chunks = _chunk_markdown_sections(text)
            for ci, chunk in enumerate(chunks):
                chunk_key = rel_key if len(chunks) == 1 else f"{rel_key}:{ci}"
                docs.append(
                    {
                        "rel_key": chunk_key,
                        "title": f"{title} ({source_type})",
                        "text": chunk,
                        "metadata": {
                            "source_type": source_type,
                            "protocol": "",
                            "attack_vector": vector,
                            "chain": chain,
                            "severity": "medium",
                            "source_url": url_tpl.format(f=rel),
                            "rel_path": rel,
                        },
                    }
                )
            if limit and len(docs) >= limit:
                return docs
    return docs


async def _swc_docs(limit: int | None) -> list[dict[str, Any]]:
    """Fetch SWC registry markdown entries (weakness taxonomy)."""
    ids = _SWC_IDS[:limit] if limit else _SWC_IDS
    docs: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        for swc in ids:
            try:
                r = await c.get(_SWC_MD.format(swc=swc))
                if r.status_code != 200:
                    continue
                text = r.text
            except Exception as e:
                logger.warning("swc_fetch_fail %s: %s", swc, str(e)[:100])
                continue
            title = swc
            m = re.search(r"^#\s+(.+)$", text, re.M)
            if m:
                title = m.group(1).strip()
            vector = _SWC_VECTORS.get(swc, _detect_attack_vector(text[:2000]))
            chunks = _chunk_markdown_sections(text)
            for ci, chunk in enumerate(chunks):
                chunk_key = f"swc:{swc}:{ci}" if len(chunks) > 1 else f"swc:{swc}"
                docs.append(
                    {
                        "rel_key": chunk_key,
                        "title": f"{swc} — {title}",
                        "text": chunk,
                        "metadata": {
                            "source_type": "swc_registry",
                            "protocol": swc,
                            "attack_vector": vector,
                            "chain": "EVM",
                            "severity": "high",
                            "source_url": f"https://github.com/SmartContractSecurity/SWC-registry/blob/master/entries/{swc}.md",
                        },
                    }
                )
    return docs


async def _defillama_hacks_docs(limit: int | None) -> list[dict[str, Any]]:
    """Ingest the DeFiLlama hacks database (600+ incidents).

    Finishes the Rekt/exploit database coverage with a structured,
    maintained incident database (date, amount, technique, classification,
    chain, language).
    """
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.get(DEFILLAMA_HACKS_API)
            if r.status_code != 200:
                logger.warning("defillama_hacks_fetch status=%d", r.status_code)
                return docs
            hacks = r.json() or []
    except Exception as e:
        logger.warning("defillama_hacks_error: %s", str(e)[:150])
        return docs
    if limit:
        hacks = hacks[:limit]
    for hack in hacks:
        if not isinstance(hack, dict):
            continue
        ts = float(hack.get("date") or 0)
        date_iso = datetime.fromtimestamp(ts, tz=UTC).date().isoformat() if ts > 0 else ""
        name = str(hack.get("name", "")).strip() or "unknown"
        amount = float(hack.get("amount") or 0)
        chains = hack.get("chain") or []
        chain = str(chains[0]).lower() if isinstance(chains, list) and chains else "EVM"
        technique = str(hack.get("technique") or "").strip()
        classification = str(hack.get("classification") or "").strip()
        vector = _detect_attack_vector(f"{name} {technique} {classification}")
        text = (
            f"Title: {name}\n"
            f"Date: {date_iso or 'unknown'}\n"
            f"Funds lost: {amount if amount else 'unknown'}\n"
            f"Technique: {technique or 'unknown'}\n"
            f"Classification: {classification or 'unknown'}\n"
            f"Chain: {chain}\n"
            f"Language: {hack.get('language') or 'unknown'}"
        )
        docs.append(
            {
                "rel_key": (
                    f"defillama_hacks:{re.sub(r'[^a-z0-9]+', '-', name.lower())}:{date_iso}"
                ),
                "title": f"DeFi hack: {name}",
                "text": text,
                "metadata": {
                    "source_type": "defillama_hacks",
                    "protocol": name,
                    "date": date_iso,
                    "date_ts": _date_to_ts(date_iso),
                    "attack_vector": vector,
                    "chain": chain,
                    "amount_lost_usd": amount if amount else None,
                    "severity": (
                        "critical" if amount >= 1e7 else ("high" if amount >= 1e6 else "medium")
                    ),
                    "source_url": "https://defillama.com/hacks",
                    "technique": technique,
                    "classification": classification,
                },
            }
        )
    logger.info("defillama_hacks_docs=%d", len(docs))
    return docs


__all__ = [
    "_defillama_hacks_docs",
    "_slowmist_docs",
    "_swc_docs",
]
