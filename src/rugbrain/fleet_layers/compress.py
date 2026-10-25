"""Compression layer: LLMLingua prose compression (:8077).

The service has no OpenAPI and no ``/docs``; routes are probed in order and the
first non-404 is used. If no route answers, or the service echoes the input
("verify-or-return"), the adapter REFUSES — it never returns the raw text as
if it had been compressed. A cold model load can exceed the 5 s budget: that is a
typed ``timeout`` refusal, never a silent hang, and the next warm call succeeds.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any

from rugbrain.fleet_layers.envelope import layer_response
from rugbrain.fleet_layers.zoekt import _http_json

SOURCE = "cc-compress"
BASE = os.getenv("RB_COMPRESS_BASE", "http://127.0.0.1:8077")
TIMEOUT = float(os.getenv("RB_COMPRESS_TIMEOUT", "5"))
CANDIDATES = tuple(
    p.strip() for p in os.getenv("RB_COMPRESS_PATHS", "/compress,/v1/compress,/api/compress,/api/v1/compress,/compress_text").split(",") if p.strip()
)
FALLBACK = "caller keeps the RAW text — this layer did not compress anything"


def compress(text: str, ratio: float = 0.5) -> dict[str, Any]:
    """Compress prose with LLMLingua; typed refusal when unavailable.

    Returns the shared envelope; ``data.compressed`` is the compressed string and
    ``data.ratio_actual`` the measured output/input character ratio.
    """
    if not text or not text.strip():
        return layer_response(source=SOURCE, error_code="refused", detail="text is empty", fallback=FALLBACK)
    probed: list[str] = []
    first_error: tuple[str, str] | None = None
    for path in CANDIDATES:
        url = f"{BASE}{path}"
        probed.append(path)
        try:
            status, _headers, body = _http_json(url, payload={"text": text, "ratio": ratio}, timeout=TIMEOUT)
        except Exception as exc:
            code = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "unavailable"
            first_error = first_error or (code, f"{path}: {type(exc).__name__}: {exc}")
            continue
        if status == 404:
            continue
        if status in (401, 403):
            return layer_response(source=SOURCE, error_code="unauthorized", detail=f"{path}: HTTP {status}", fallback=FALLBACK)
        if status != 200:
            return layer_response(source=SOURCE, error_code="bad_response", detail=f"{path}: HTTP {status}", fallback=FALLBACK)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return layer_response(source=SOURCE, error_code="bad_response", detail=f"{path}: non-JSON body", fallback=FALLBACK)
        out = parsed.get("compressed_prompt") or parsed.get("compressed") or parsed.get("text")
        if not isinstance(out, str) or not out.strip():
            return layer_response(source=SOURCE, error_code="bad_response", detail=f"{path}: no compressed field in payload", fallback=FALLBACK)
        if out.strip() == text.strip():
            return layer_response(
                source=SOURCE,
                error_code="verify_or_return",
                detail=f"{path}: service returned the original text, so nothing was compressed",
                fallback=FALLBACK,
            )
        return layer_response(
            source=SOURCE,
            data={
                "compressed": out,
                "ratio_requested": ratio,
                "ratio_actual": round(len(out) / max(len(text), 1), 3),
                "origin_tokens": parsed.get("origin_tokens"),
                "compressed_tokens": parsed.get("compressed_tokens"),
                "endpoint": path,
            },
        )
    code, detail = first_error or ("unavailable", "no route answered")
    return layer_response(source=SOURCE, error_code=code, detail=f"{detail}; probed {probed}", fallback=FALLBACK)
