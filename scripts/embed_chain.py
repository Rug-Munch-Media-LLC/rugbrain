"""Ordered embedding lanes for the corpus — a fallback chain, not one lane.

Why this exists: on 2026-10-23 DeepInfra started returning HTTP 402 (overdue
invoices) and the corpus stopped advancing while the refresh job kept exiting 0
with ``upserted=0``. Provider selection used to pick exactly ONE lane, so a
billed-out or restarted lane was a single point of failure with no alarm.

First healthy lane wins:

    local-8b     llama.cpp :8024   Qwen3-Embedding-8B  4096-d  (no billing)
    deepinfra    api.deepinfra     Qwen/Qwen3-Embedding-8B 4096-d
    siliconflow  api.siliconflow   Qwen/Qwen3-Embedding-8B 4096-d

Every lane is checked against ``embed_contract`` (4096-d) and any lane whose
port serves a different vector space is refused outright.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

from embed_contract import CORPUS_DIM, NON_CORPUS_PORTS, EmbedLaneExhausted, port_of

logger = logging.getLogger("rugbrain.embed_chain")

COOLDOWN_S = float(os.getenv("RUG_EMBED_COOLDOWN_S", "600"))
FAILURE_FILE = os.getenv(
    "RUG_EMBED_FAILURE_FILE", "/var/log/rugbrain/embed-lane-failures.jsonl"
)
DEFAULT_ORDER = ("siliconflow", "deepinfra", "local")


@dataclass(frozen=True, slots=True)
class Lane:
    name: str
    url: str
    model: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 120.0


_cooldown: dict[str, float] = {}
_failures = 0
_failure_detail = ""


def failure_count() -> int:
    """Failed lane attempts in this process (0 == a clean sweep)."""
    return _failures


def last_failure() -> str:
    return _failure_detail


def reset_failures() -> None:
    global _failures, _failure_detail
    _failures = 0
    _failure_detail = ""


def endpoint(base: str) -> str:
    """Accept both ``.../v1`` and ``.../v1/embeddings`` spellings.

    Both are written across the fleet's env files; appending blindly produced
    ``POST .../v1/embeddings/embeddings`` -> 404 and silently failed every
    hosted embed (seen 2026-09-28 and 2026-10-22).
    """
    base = base.rstrip("/")
    return base if base.endswith("/embeddings") else f"{base}/embeddings"


def _local_lane() -> Lane:
    headers = {"Content-Type": "application/json"}
    key = os.getenv("LLAMA_EMBED_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return Lane(
        name="local",
        url=endpoint(os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8024/v1")),
        model=os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b"),
        headers=headers,
        timeout=float(os.getenv("RAG_EMBED_TIMEOUT", "300")),
    )


def _hosted_lane(
    name: str, url_env: str, default_url: str, model_env: str, key: str
) -> Lane | None:
    if not key:
        return None
    url = os.getenv(url_env, "").strip() or default_url
    if not url.startswith(("http://", "https://")):
        logger.error("embed_lane_skipped lane=%s reason=bad_url url=%r", name, url)
        return None
    return Lane(
        name=name,
        url=endpoint(url),
        model=os.getenv(model_env, "Qwen/Qwen3-Embedding-8B"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=float(os.getenv("RUG_EMBED_HOSTED_TIMEOUT", "120")),
    )


def _deepinfra_key() -> str:
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if key:
        return key
    path = os.getenv("DEEPINFRA_KEY_FILE", "/run/secrets/deepinfra_api_key")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _build(name: str) -> Lane | None:
    if name == "local":
        return _local_lane()
    if name == "deepinfra":
        return _hosted_lane(
            "deepinfra",
            "DEEPINFRA_EMBED_URL",
            "https://api.deepinfra.com/v1/embeddings",
            "DEEPINFRA_EMBED_MODEL",
            _deepinfra_key(),
        )
    if name == "siliconflow":
        return _hosted_lane(
            "siliconflow",
            "SILICONFLOW_EMBED_URL",
            "https://api.siliconflow.com/v1/embeddings",
            "SILICONFLOW_EMBED_MODEL",
            os.getenv("SILICONFLOW_API_KEY", "").strip(),
        )
    return None


def build_chain(preferred: str | None = None) -> list[Lane]:
    """Return the ordered lane chain; ``preferred`` first when it is usable.

    Raises ``EmbedLaneExhausted`` when no 4096-d lane is configured — a bulk
    ingest with no lane must fail before it fetches anything.
    """
    order = [preferred] if preferred in DEFAULT_ORDER else []
    order += [name for name in DEFAULT_ORDER if name != preferred]
    lanes: list[Lane] = []
    for name in order:
        lane = _build(name)
        if lane is None:
            continue
        if port_of(lane.url) in NON_CORPUS_PORTS:
            logger.error(
                "embed_lane_refused lane=%s url=%s reason=non_corpus_dimension want=%d",
                lane.name,
                lane.url,
                CORPUS_DIM,
            )
            continue
        lanes.append(lane)
    if not lanes:
        raise EmbedLaneExhausted(
            "no corpus embedding lane configured: need one of local(:8024 + LLAMA_EMBED_KEY), "
            f"deepinfra(key) or siliconflow(key); ports {sorted(NON_CORPUS_PORTS)} are non-corpus"
        )
    return lanes


def record_failure(lane: str, status: str, detail: str) -> None:
    """Bump the counter and append one line the wrapper/monitor can read."""
    global _failures, _failure_detail
    _failures += 1
    _failure_detail = f"{lane}:{status}:{detail}"[:300]
    logger.warning("embed_lane_fail lane=%s status=%s detail=%s", lane, status, detail[:200])
    try:
        os.makedirs(os.path.dirname(FAILURE_FILE) or ".", exist_ok=True)
        with open(FAILURE_FILE, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": int(time.time()),
                        "lane": lane,
                        "status": status,
                        "detail": detail[:300],
                        "want_dim": CORPUS_DIM,
                    }
                )
                + "\n"
            )
    except OSError as exc:  # a full disk must not hide the failure
        logger.error("embed_failure_file_unwritable path=%s err=%s", FAILURE_FILE, exc)


def cooling(lane: Lane) -> bool:
    """True while a lane that just failed permanently (401/402) is parked."""
    return _cooldown.get(lane.name, 0.0) > time.monotonic()


def freeze(lane: Lane) -> None:
    _cooldown[lane.name] = time.monotonic() + COOLDOWN_S
