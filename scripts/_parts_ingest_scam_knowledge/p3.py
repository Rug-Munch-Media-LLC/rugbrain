"""Part 3/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F403
from _parts_ingest_scam_knowledge.p1 import _normalize_severity
from _parts_ingest_scam_knowledge.p2 import _content_hash, _point_id


def _build_point(
    source: str,
    collection: str,
    rel_key: str,
    content: str,
    metadata: dict[str, Any],
    vector: list[float],
) -> dict[str, Any]:
    point_id = _point_id(source, rel_key, _content_hash(content))
    payload = {
        "content": content[:10000],
        "metadata": metadata or {},
        "source": source,
        "severity": _normalize_severity(metadata.get("severity", "unknown")),
        "chain": metadata.get("chain", "") or "",
        "collection": collection,
    }
    # Surface structured fields at payload top-level so /api/v1/search
    # filters (severity/chain/attack_vector/protocol/source_type) work.
    for key in ("attack_vector", "protocol", "source_type", "date", "date_ts", "amount_lost_usd"):
        if metadata.get(key) is not None:
            payload[key] = metadata[key]
    return {"id": point_id, "vector": vector, "payload": payload}


def _clone_repo(url: str, name: str, subdirs: tuple[str, ...] | None = None) -> Path:
    """Shallow-clone *url* into KNOWLEDGE_DIR/<name> keeping only *subdirs*."""
    target = KNOWLEDGE_DIR / name
    if (target / ".git").exists():
        logger.info("repo already present: %s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", url, target)
    tmp = Path(tempfile.mkdtemp(prefix=f"{name}-clone-"))
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(tmp)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr[-500:]}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        if subdirs:
            for sub in subdirs:
                src_sub = tmp / sub
                if src_sub.exists():
                    shutil.move(str(src_sub), str(target / sub))
        else:
            for item in tmp.iterdir():
                if item.name == ".git":
                    continue
                shutil.move(str(item), str(target / item.name))
        (target / ".git").mkdir()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    logger.info("clone complete name=%s", name)
    return target


def _extract_description(code: str) -> str:
    """Extract comment lines at the top of a Solidity file as a description."""
    lines: list[str] = []
    for line in code.splitlines()[:80]:
        m = _COMMENT_LINES_RE.match(line)
        if m:
            text = m.group(2).strip()
            if text:
                lines.append(text)
        elif lines:
            break
    return " ".join(lines)[:1000]


def _parse_defihacklabs_incidents(repo_root: Path) -> dict[str, dict[str, str]]:
    """Parse past/*/README.md incident lists → {normalized protocol: metadata}."""
    incidents: dict[str, dict[str, str]] = {}
    readme_pattern = re.compile(r"^###\s+(\d{8})\s+(.+?)(?:\s+-\s+(.+))?$")
    lost_pattern = re.compile(r"^###\s+Lost:\s*(.+)$")
    for readme in (repo_root / "past").rglob("README.md"):
        current: dict[str, str] | None = None
        for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            m = readme_pattern.match(line)
            if m:
                key = re.sub(r"[^a-z0-9]+", "", m.group(2).lower())
                if key:
                    current = {
                        "date": m.group(1),
                        "protocol": m.group(2).strip(),
                        "attack_type": (m.group(3) or "").strip(),
                    }
                    incidents.setdefault(key, current)
                continue
            m = lost_pattern.match(line)
            if m and current:
                current["amount"] = m.group(1).strip()
    return incidents


__all__ = [
    "_build_point",
    "_clone_repo",
    "_extract_description",
    "_parse_defihacklabs_incidents",
]
