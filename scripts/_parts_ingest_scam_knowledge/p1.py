"""Part 1/10 of scripts/ingest_scam_knowledge.py."""

from __future__ import annotations

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F401,F403
from _parts_ingest_scam_knowledge._h2 import *  # noqa: F401,F403

def _detect_attack_vector(text: str) -> str:
    for pattern, vector in _CONTENT_VECTORS:
        if pattern.search(text or ""):
            return vector
    return "unknown"


def _detect_chain(text: str) -> str:
    lowered = f" {text.lower()} "
    for chain, aliases in CHAIN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                return chain
    return ""


def _normalize_severity(value: Any) -> str:
    syn: dict[str, str] = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "moderate": "medium",
        "low": "low",
        "info": "info",
        "informational": "info",
        "unknown": "unknown",
    }
    key = str(value or "").strip().lower()
    if key in syn:
        return syn[key]
    if "crit" in key:
        return "critical"
    if "high" in key:
        return "high"
    if "medium" in key or "moderate" in key:
        return "medium"
    if "low" in key:
        return "low"
    return "unknown"


def _date_to_ts(date_iso: str) -> float | None:
    """Parse YYYY-MM-DD (or YYYYMMDD / MM/DD/YYYY) into a unix timestamp."""
    if not date_iso:
        return None
    s = str(date_iso).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
    return None


def _normalize_date(date_iso: Any) -> str:
    s = str(date_iso or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _parse_amount_lost(text: Any) -> float | None:
    """Parse '$200M', '$3.5 billion', '2000000', '$1.2m' → USD float."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).replace(",", "").replace("$", "").strip().lower()
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*(b|bn|billion|m|mn|million|k|thousand|t|trillion)?", s)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    mult = {
        "b": 1e9,
        "bn": 1e9,
        "billion": 1e9,
        "t": 1e12,
        "trillion": 1e12,
        "m": 1e6,
        "mn": 1e6,
        "million": 1e6,
        "k": 1e3,
        "thousand": 1e3,
    }
    suffix = m.group(2) or ""
    if suffix in mult:
        value *= mult[suffix]
    elif value < 1000:
        # bare number without suffix is already in USD
        pass
    return round(value, 2)


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into fixed-width character windows with overlap."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _chunk_markdown_sections(text: str, max_len: int = CHUNK_SIZE) -> list[str]:
    """Split markdown into sections by headings, then window long sections."""
    text = (text or "").strip()
    if not text:
        return []
    sections = re.split(r"(?m)^(?=#{1,3}\s)", text)
    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= max_len:
            chunks.append(section)
        else:
            chunks.extend(_chunk_text(section, size=max_len))
    return chunks


__all__ = [
    "_detect_attack_vector",
    "_detect_chain",
    "_normalize_severity",
    "_date_to_ts",
    "_normalize_date",
    "_parse_amount_lost",
    "_chunk_text",
    "_chunk_markdown_sections",
]
