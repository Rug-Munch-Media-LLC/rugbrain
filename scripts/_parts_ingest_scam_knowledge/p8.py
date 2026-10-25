"""Part 8/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge.p1 import _detect_attack_vector, _detect_chain
from _parts_ingest_scam_knowledge.p3 import _clone_repo
from _parts_ingest_scam_knowledge.p4 import _defihacklabs_docs, _fetch_rekt_leaderboard, _rekt_docs
from _parts_ingest_scam_knowledge.p5 import _cryptoscamdb_docs
from _parts_ingest_scam_knowledge.p6 import _defillama_hacks_docs, _slowmist_docs, _swc_docs
from _parts_ingest_scam_knowledge.p7 import _immunefi_docs, _web3hacks_docs

async def _solodit_docs(limit: int | None) -> list[dict[str, Any]]:
    """Scrape audit report listings from Solodit (best-effort).

    Solodit aggregates public smart-contract audit reports — including
    OpenZeppelin, Code4rena, Spearbit and many others — into one listing.
    We extract report titles/links from the rendered HTML.
    """
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
            r = await c.get(SOLODIT_URL, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                logger.warning("solodit_fetch status=%d", r.status_code)
                return docs
            html = r.text
    except Exception as e:
        logger.warning("solodit_error: %s", str(e)[:150])
        return docs

    anchors = re.findall(r'<a[^>]*href="(/reports/[^"]+)"[^>]*>([^<]{5,120})</a>', html)
    seen: set[str] = set()
    count = 0
    for href, title in anchors:
        title = re.sub(r"\s+", " ", title).strip()
        if not title or title.lower() in seen:
            continue
        if any(w in title.lower() for w in ("read more", "view all", "loading", "load more")):
            continue
        seen.add(title.lower())
        count += 1
        vector = _detect_attack_vector(title)
        docs.append(
            {
                "rel_key": f"openzeppelin_audits:{re.sub(r'[^a-z0-9]+', '-', title.lower())}",
                "title": f"Audit report: {title}",
                "text": (
                    f"Public smart-contract audit report: {title}. "
                    "Audit reports document findings, vulnerability classes, "
                    "and fixes for reviewed protocols."
                ),
                "metadata": {
                    "source_type": "openzeppelin_audits",
                    "protocol": title.split(" by ")[0].strip(),
                    "attack_vector": vector,
                    "chain": "multi",
                    "severity": "medium",
                    "source_url": f"{SOLODIT_URL}{href}",
                },
            }
        )
        if limit and count >= limit:
            break
    if not docs:
        logger.warning("solodit_parse_empty — page format may have changed")
    logger.info("solodit_docs=%d", len(docs))
    return docs


async def _postmortems_docs(limit: int | None) -> list[dict[str, Any]]:
    """Fetch post-mortem articles for the most recent Rekt leaderboard hacks.

    Rekt article bodies are fetched via the Web Archive (a stable mirror of
    ``rekt.news/{slug}/``) and chunked per incident. Best-effort:
    unreachable articles are skipped.
    """
    limit = min(limit or 5, 10)
    try:
        entries = await _fetch_rekt_leaderboard()
    except Exception as e:
        logger.warning("postmortems_rekt_fetch_failed: %s", str(e)[:150])
        return []
    base = REKT_LEADERBOARD_URL.rsplit("/", 1)[0]
    docs: list[dict[str, Any]] = []
    fetched = 0
    for entry in entries:
        slug = str(entry.get("slug", "")).strip()
        title = str(entry.get("title", "")).strip()
        if not slug or not title:
            continue
        url = f"https://web.archive.org/web/2/{base}/{slug}/"
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
                r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200:
                    continue
                text = _html_to_text(r.text)
        except Exception as e:
            logger.debug("postmortem_fetch_failed slug=%s err=%s", slug, str(e)[:100])
            continue
        if len(text) < 200:
            continue
        vector = _detect_attack_vector(text[:2000])
        chain = _detect_chain(f"{title} {text[:2000]}")
        chunks = smart_split(text)
        for ci, chunk in enumerate(chunks):
            chunk_key = f"postmortems:{slug}:{ci}" if len(chunks) > 1 else f"postmortems:{slug}"
            docs.append(
                {
                    "rel_key": chunk_key,
                    "title": f"Post-mortem: {title}",
                    "text": chunk,
                    "metadata": {
                        "source_type": "postmortems",
                        "protocol": title,
                        "attack_vector": vector,
                        "chain": chain,
                        "severity": "high",
                        "source_url": url,
                        "slug": slug,
                    },
                }
            )
        fetched += 1
        if limit and fetched >= limit:
            break
    logger.info("postmortems_docs=%d", len(docs))
    return docs


def _html_to_text(html: str) -> str:
    """Minimal HTML→text: strip scripts/styles/tags and collapse whitespace."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def _collect_docs(
    source: str,
    limit: int | None,
    fetch_rekt: bool,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    if source in SOURCE_TABLE:
        repo = SOURCE_TABLE[source]
        target = KNOWLEDGE_DIR / repo[1]
        if not target.exists():
            target = _clone_repo(repo[0], repo[1], repo[2])
        elif refresh:
            git_refresh(str(target))
        if source == "defihacklabs":
            return _defihacklabs_docs(target, limit)
        if source == "cryptoscamdb":
            return _cryptoscamdb_docs(target, limit)
        if source == "slowmist":
            return _slowmist_docs(target, limit)
        if source == "web3hacks":
            return _web3hacks_docs(target, limit)
    if source == "rekt_news":
        if not fetch_rekt:
            return []
        entries = await _fetch_rekt_leaderboard()
        return _rekt_docs(entries, limit)
    if source == "swc_registry":
        return await _swc_docs(limit)
    if source == "defillama_hacks":
        return await _defillama_hacks_docs(limit)
    if source == "immunefi":
        return await _immunefi_docs(limit)
    if source == "openzeppelin_audits":
        return await _solodit_docs(limit)
    if source == "postmortems":
        return await _postmortems_docs(limit)
    raise ValueError(f"unknown source: {source}")


__all__ = [
    "_solodit_docs",
    "_postmortems_docs",
    "_html_to_text",
    "_collect_docs",
]
