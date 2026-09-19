"""Input guardrail — prompt-injection detection / neutralization."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._config import logger

# Instruction-override / role-switch phrases. Matched case-insensitively.
# Each entry is a (reason_code, compiled_pattern). The regexes are written
# to catch the classic jailbreak families:
#   * "ignore previous instructions" / "disregard all prior prompts"
#   * "system:" / "assistant:" / "human:" role labels
#   * "you are now" / "act as" / "pretend to be" role switches
#   * "reveal your system prompt" / "repeat your instructions"
#   * fenced code blocks that contain instruction-like text
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+"
            r"(?:all\s+)?(?:previous|prior|above|earlier|old|original|"
            r"system|initial|developer)?\s*(?:instructions?|prompts?|"
            r"messages?|content|context|rules?|guidelines?|orders?|text)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ignore_everything",
        re.compile(r"\bignore\s+(?:everything|all\b)", re.IGNORECASE),
    ),
    (
        "system_override",
        re.compile(
            r"\b(?:override|replace|change|update)\s+(?:your|the)\s+"
            r"(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?|settings?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_switch",
        re.compile(
            r"\b(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you\s+are)|"
            r"you\s+will\s+now|you\s+must\s+now|from\s+now\s+on)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_role_label",
        re.compile(r"(?:^|\n)\s*(?:system|assistant|developer|human)\s*:", re.IGNORECASE),
    ),
    (
        "marker_injection",
        re.compile(r"(?:<<\s*SYS\s*>>|<\|(?:system|im_start|im_end)\|>|\[/?INST\])", re.IGNORECASE),
    ),
    (
        "prompt_exfil",
        re.compile(
            r"\b(?:reveal|show|print|display|output|repeat|return|tell\s+me|"
            r"what\s+are|write\s+out)\s+(?:your|the|all\s+your)\s+"
            r"(?:system\s+|initial\s+|developer\s+)?"
            r"(?:prompt|instructions?|rules?|guidelines?|system\s+prompt)\b",
            re.IGNORECASE,
        ),
    ),
)

# Fenced code block detection: ```lang ... ``` or ~~~ ... ~~~. Used to spot
# instruction text smuggled inside a code block (a common prompt-injection
# vehicle when answers echo retrieved code verbatim).
_FENCE_RE = re.compile(r"(?:```|~~~)(.*?)(?:```|~~~)", re.DOTALL)

# Neutralization targets that we strip from the query (substring removal).
# Kept separate from detection so we never silently rewrite a legit query
# that merely *mentions* a jailbreak ("how do I defend against prompt
# injection?") — detection still flags it, stripping is conservative.
_STRIP_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+"
    r"(?:all\s+)?(?:previous|prior|above|earlier|old|original|system|initial|developer)?\s*"
    r"(?:instructions?|prompts?|messages?|content|context|rules?|guidelines?|orders?|text)\b"
    r"|\b(?:override|replace|change|update)\s+(?:your|the)\s+"
    r"(?:system\s+)?(?:prompt|instructions?|rules?|guidelines?|settings?)\b"
    r"|\b(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you\s+are)|you\s+will\s+now|"
    r"you\s+must\s+now|from\s+now\s+on)\b"
    r"|\b(?:reveal|show|print|display|output|repeat|return|tell\s+me|"
    r"what\s+are|write\s+out)\s+(?:your|the|all\s+your)\s+"
    r"(?:system\s+|initial\s+|developer\s+)?"
    r"(?:prompt|instructions?|rules?|guidelines?|system\s+prompt)\b"
    r"|(?:^|\n)\s*(?:system|assistant|developer|human)\s*:"
    r"|(?:<<\s*SYS\s*>>|<\|(?:system|im_start|im_end)\|>|\[/?INST\])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SanitizedQuery:
    """Result of ``sanitize_query``.

    ``query`` is the (possibly cleaned) query safe to forward. ``flagged``
    is True when any injection pattern matched. ``reasons`` lists the
    human-readable pattern families that matched (empty when unflagged).
    Fail-closed: callers may refuse when ``flagged``, or forward the
    cleaned ``query``.
    """

    query: str
    flagged: bool
    reasons: tuple[str, ...] = ()


def _strip_fenced_instructions(text: str) -> str:
    """Remove fenced code blocks that contain instruction-like text."""
    if "```" not in text and "~~~" not in text:
        return text
    out_parts: list[str] = []
    last = 0
    for m in _FENCE_RE.finditer(text):
        inner = m.group(1) or ""
        if _INJECTION_PATTERNS[0][1].search(inner) or _INJECTION_PATTERNS[3][1].search(inner):
            out_parts.append(text[last : m.start()])
            last = m.end()
    if not out_parts:
        return text
    out_parts.append(text[last:])
    return "".join(out_parts)


def sanitize_query(query: str) -> SanitizedQuery:
    """Detect and neutralize prompt-injection patterns in *query*.

    Returns a :class:`SanitizedQuery`. When the query is flagged, the
    returned ``query`` is stripped of the matched instruction-override /
    role-switch phrases and fenced injection blocks, and whitespace is
    collapsed. Callers decide whether to refuse (fail-closed) or forward
    the cleaned query.
    """
    from rugbrain import guardrails as _pkg

    if not _pkg._GUARDRAILS_ENABLED:
        return SanitizedQuery(query=query, flagged=False)
    if not query:
        return SanitizedQuery(query=query, flagged=False)

    matched: list[str] = []
    for reason, pattern in _INJECTION_PATTERNS:
        if pattern.search(query):
            matched.append(reason)

    if not matched:
        return SanitizedQuery(query=query, flagged=False)

    cleaned = _pkg._strip_fenced_instructions(query)  # drop injected fenced blocks first
    cleaned = _STRIP_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t\n\r;:,")
    reasons = tuple(dict.fromkeys(matched))  # dedupe, preserve order
    logger.info("guardrails: query flagged reasons=%s", ",".join(reasons))
    return SanitizedQuery(query=cleaned, flagged=True, reasons=reasons)
