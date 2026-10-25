"""Part 7/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F403
from _parts_ingest_scam_knowledge.p1 import _detect_attack_vector, _detect_chain
from _parts_ingest_scam_knowledge.p3 import _extract_description


def _web3hacks_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Parse Cyfrin ``sc-exploits-minimized`` Foundry PoCs (src/*.sol).

    A maintained, public collection of minimized smart-contract exploits —
    each file is a reproducible exploit PoC, ideal for exploit-pattern RAG.
    """
    docs: list[dict[str, Any]] = []
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return []
    files = sorted(src_dir.rglob("*.sol"))
    files = [f for f in files if f.name not in _SKIP_SOL]
    if limit:
        files = files[:limit]
    for f in files:
        try:
            code = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not code.strip():
            continue
        rel_path = f.relative_to(repo_root).as_posix()
        contract_match = _CONTRACT_RE.search(code)
        contract_name = contract_match.group(1) if contract_match else f.stem
        description = _extract_description(code)
        vector = _detect_attack_vector(f"{rel_path} {code[:2000]}")
        chain = _detect_chain(rel_path)
        title = f"Web3 exploit PoC: {contract_name}"
        base_metadata: dict[str, Any] = {
            "source_type": "web3hacks",
            "protocol": contract_name,
            "contract_name": contract_name,
            "attack_vector": vector,
            "chain": chain or "EVM",
            "severity": "high",
            "source_url": (f"https://github.com/Cyfrin/sc-exploits-minimized/blob/main/{rel_path}"),
            "rel_path": rel_path,
        }
        chunks = smart_split(code)
        for ci, chunk in enumerate(chunks):
            chunk_key = f"{rel_path}:{ci}" if len(chunks) > 1 else rel_path
            meta = dict(base_metadata)
            if description:
                meta["description"] = description[:500]
            docs.append(
                {
                    "rel_key": f"web3hacks:{chunk_key}",
                    "title": title,
                    "text": chunk,
                    "metadata": meta,
                }
            )
    logger.info("web3hacks_docs=%d", len(docs))
    return docs


async def _immunefi_docs(limit: int | None) -> list[dict[str, Any]]:
    """Scrape the Immunefi bug-bounty explore page (best-effort).

    The page is a Next.js RSC app; we pull text fragments from its
    ``self.__next_f.push`` chunks and extract bounty program names. This is
    intentionally conservative: if the page format changes, the source
    degrades to a warning instead of failing the whole ingest.
    """
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
            r = await c.get(IMMUNEFI_EXPLORE_URL, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                logger.warning("immunefi_fetch status=%d", r.status_code)
                return docs
            html = r.text
    except Exception as e:
        logger.warning("immunefi_error: %s", str(e)[:150])
        return docs

    fragments: list[str] = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html)
    text = " ".join(fragments)
    candidates = re.findall(
        r"([A-Z][A-Za-z0-9][A-Za-z0-9 .&'-]{2,60}?)\s*(?:bug bounty|bounty)", text
    )
    candidates = [
        c.strip()
        for c in candidates
        if len(c.strip()) >= 4 and c.strip().lower() not in _IMMUNEFI_STOPWORDS
    ]
    # Deduplicate preserving order.
    candidates = list(dict.fromkeys(candidates))
    if limit:
        candidates = candidates[:limit]
    for name in candidates:
        vector = _detect_attack_vector(name)
        docs.append(
            {
                "rel_key": f"immunefi:{re.sub(r'[^a-z0-9]+', '-', name.lower())}",
                "title": f"Immunefi bounty program: {name}",
                "text": (
                    f"Immunefi bug bounty program: {name}. In-scope vulnerability "
                    "research can earn bounties; attack surfaces and vulnerability "
                    "classes are documented on the program page."
                ),
                "metadata": {
                    "source_type": "immunefi",
                    "protocol": name,
                    "attack_vector": vector,
                    "chain": "multi",
                    "severity": "medium",
                    "source_url": IMMUNEFI_EXPLORE_URL,
                },
            }
        )
    if not docs:
        logger.warning("immunefi_parse_empty — page format may have changed")
    logger.info("immunefi_docs=%d", len(docs))
    return docs


__all__ = [
    "_immunefi_docs",
    "_web3hacks_docs",
]
