"""Numeric facts buried in the scythe-radar archive's bilingual free text.

Split from ``scythe.py`` purely for the 200-line cap — the projection and the
prose parsing are two different jobs and deserve two modules.

Three figures in that archive exist ONLY inside ``victims_note``, and each one
is a place where a naive parser silently corrupts the corpus:

  * holder counts, in three written forms (``链上持有人 103798``,
    ``约 30 万持有人``, ``about 300k holders``) with 万 (10^4) and 亿 (10^8)
    magnitude suffixes;
  * a documented cash-out (``单次砸盘套现 $2626 万`` = 26.26M);
  * a peak TVL (``峰值 TVL $4590 万`` = 45.9M) that has the SAME surface shape
    as the cash-out and must never be recorded as a loss.

Approximation is preserved rather than smoothed: ``parse_holders`` returns
whether the figure was exact, so "about 300k" is never presented as measured.
"""

from __future__ import annotations

import re

_HOLDERS_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*([万亿]?)\s*holders?|持有人数?\s*([\d,]+(?:\.\d+)?)\s*([万亿]?)",
    re.I,
)
# "约 30 万持有人" — the magnitude word comes BEFORE the number here, so the
# generic pattern cannot see the 持有人 tail. Separate, and the only shape in
# the archive that is explicitly approximate.
_HOLDERS_CN_APPROX_RE = re.compile(r"约\s*([\d,]+(?:\.\d+)?)\s*([万亿]?)\s*持有人", re.I)
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([万亿KkMmBb]?)")
_PEAK_RE = re.compile(
    r"(?:峰值\s*TVL|peak\s*TVL)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([万亿KkMmBb]?)", re.I
)

#: Chinese magnitude suffixes. 万 = 10^4, 亿 = 10^8.
CN_SCALE = {"万": 10_000, "亿": 100_000_000}
#: English suffixes, as written in the archive's mixed text.
EN_SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "": 1}


def scaled(number: str, suffix: str) -> float | None:
    """Apply a 万/亿 or K/M/B suffix to a parsed number; None when unparseable."""
    try:
        value = float(str(number).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not suffix:
        return value
    return value * CN_SCALE.get(suffix, EN_SCALE.get(suffix.lower(), 1))


def parse_holders(note: str) -> tuple[int | None, bool]:
    """Holder count from a free-text note -> ``(count, exact)``.

    ``exact`` is False for an explicitly approximate figure ("约 30 万" /
    "about 300k"), so the caller can record that the number is not measured.
    """
    if not note:
        return None, False
    m = _HOLDERS_RE.search(note)
    if m:
        number, cn_suffix = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        exact = "约" not in note and "about" not in note.lower()
    else:
        m = _HOLDERS_CN_APPROX_RE.search(note)
        if not m:
            return None, False
        number, cn_suffix, exact = m.group(1), m.group(2), False
    value = scaled(number, cn_suffix)
    return (None, False) if value is None else (int(value), exact)


def parse_money(note: str) -> float | None:
    """A cash-out / loss figure in the note (``$2626 万`` -> 26,260,000)."""
    m = _MONEY_RE.search(note or "")
    return scaled(m.group(1), m.group(2)) if m else None


def parse_peak_tvl(note: str) -> float | None:
    """Peak TVL, kept separate from loss so a peak is never read as a loss."""
    m = _PEAK_RE.search(note or "")
    return scaled(m.group(1), m.group(2)) if m else None


__all__ = [
    "CN_SCALE",
    "EN_SCALE",
    "parse_holders",
    "parse_money",
    "parse_peak_tvl",
    "scaled",
]
