"""LLM configuration + content generation for the rmi-rag ``app`` layer.

Self-contained port of rmi-backend ``app.llm_config`` for the rmi-rag
standalone container. Provides ``generate_content()`` used by the
investigation-narrative synthesis path.

Provider chain:
  PRIMARY:   DeepSeek (cheap, fast) via LLM_API_KEY / LLM_BASE_URL
  CONTENT:   OpenRouter (free tier) via OPENROUTER_API_KEY
"""

from __future__ import annotations

import base64
import contextlib
import os

from app.core.logging import get_logger

logger = get_logger(__name__)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
if os.getenv("LLM_API_KEY_B64"):
    with contextlib.suppress(Exception):
        os.environ["LLM_API_KEY"] = base64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()
LLM_API_KEY = os.getenv("LLM_API_KEY", OPENROUTER_KEY)

DEEPSEEK_BASE = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

CONTENT_MODEL = os.getenv("RMI_CONTENT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
FAST_MODEL = os.getenv("RMI_FAST_MODEL", "deepseek-v4-flash")

AI_MODEL = os.getenv("LLM_MODEL", FAST_MODEL)
AI_BASE = os.getenv("LLM_BASE_URL", f"{DEEPSEEK_BASE}/chat/completions")
if not LLM_API_KEY:
    AI_BASE = f"{OPENROUTER_BASE}/chat/completions"


def get_content_config() -> dict:
    """Get config for content generation (OpenRouter/Nemotron primary)."""
    return {
        "model": CONTENT_MODEL,
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_KEY,
        "max_tokens": 4096,
        "temperature": 0.7,
    }


def get_fast_config() -> dict:
    """Get config for fast/cheap tasks (DeepSeek Flash primary)."""
    if LLM_API_KEY:
        return {
            "model": FAST_MODEL,
            "base_url": f"{DEEPSEEK_BASE}/chat/completions",
            "api_key": LLM_API_KEY,
            "max_tokens": 2048,
            "temperature": 0.3,
        }
    return get_content_config()  # fallback to OpenRouter


async def generate_content(
    prompt: str,
    system: str = "You are a crypto intelligence analyst. Be concise, data-driven, and trader-focused.",
    max_tokens: int = 2048,
    temperature: float = 0.7,
) -> str:
    """
    Generate content via the configured LLM provider.

    Tries OpenRouter content config first, falls back to the fast
    (DeepSeek) config on rate-limit/error, then returns a degraded
    echo of the prompt so callers always get a string back.
    """
    import httpx

    async def _call(cfg: dict) -> str:
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://rugmunch.io",
            "X-Title": "RugSense",
        }
        payload = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": min(max_tokens, cfg.get("max_tokens", max_tokens)),
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(cfg["base_url"], headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    cfg = get_content_config()
    if cfg.get("api_key"):
        try:
            return await _call(cfg)
        except Exception as exc:
            logger.warning("content_generation_failed error=%s", str(exc)[:120])
    # Try fast config
    fast_cfg = get_fast_config()
    if fast_cfg.get("api_key") and fast_cfg["api_key"] != cfg.get("api_key"):
        try:
            return await _call(fast_cfg)
        except Exception as exc:
            logger.warning("fast_generation_failed error=%s", str(exc)[:120])
    # Degraded path — never raise; narrative still shows evidence
    logger.warning("llm_unavailable — returning degraded narrative")
    return (
        f"[llm-unavailable] No LLM provider configured (OPENROUTER_API_KEY / LLM_API_KEY). "
        f"Evidence-based narrative below:\n\n{prompt[:4000]}"
    )
