"""Chatbot cutover shadow test — OpenRouter (current) vs local :8009.

Sends an identical crypto-support prompt set to both providers and reports
latency + quality heuristics side by side, with a cutover recommendation.

THIS DOES NOT SWITCH ANYTHING. Cutover is a separate, explicit step
(see docs/DUAL_TIER_RAG.md § chatbot cutover).

Usage (on Talos, rmi-rag venv):
  OPENROUTER_API_KEY=<key> python -m scripts.dual_tier.shadow_test_chat \
      --runs 3 --max-tokens 120

Quality heuristics per answer:
  * non-empty, >= 40 chars
  * no <think> leakage
  * no refusal markers ("i can't", "as an ai", "i cannot")
  * crypto-domain token presence for domain prompts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from typing import Any

import aiohttp

LOCAL_URL = os.getenv("CHAT_URL", "http://172.22.0.1:8009")
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
OPENROUTER_MODEL = os.getenv("OPENROUTER_SHADOW_MODEL", "tngtech/deepseek-r1d-671b:free")

PROMPTS = [
    (
        "domain-risk",
        "In two sentences: what are the top three signs a new token's liquidity pool is a rug pull?",
    ),
    (
        "domain-tracing",
        "How does a scammer typically move stolen funds after a rug pull? Keep it short.",
    ),
    ("general", "What is the difference between a DEX and a CEX? One paragraph."),
    (
        "grounding",
        "Context: TokenX has 85% deployer-owned LP and mint authority retained. "
        "Question: what is TokenX's main risk signal? One sentence.",
    ),
]

REFUSAL_MARKERS = ("i can't", "i cannot", "as an ai", "i'm sorry", "against my")


def score_answer(text: str, expect_domain: bool) -> dict[str, Any]:
    checks = {
        "nonempty": len(text.strip()) >= 40,
        "no_think": "<think>" not in text,
        "no_refusal": not any(m in text.lower() for m in REFUSAL_MARKERS),
    }
    if expect_domain:
        checks["domain_terms"] = any(
            w in text.lower()
            for w in (
                "liquidity",
                "token",
                "pool",
                "wallet",
                "deployer",
                "dex",
                "mixing",
                "exchange",
                "crypto",
                "blockchain",
                "mint",
                "chain",
            )
        )
    checks["passed"] = all(checks.values())
    return checks


async def ask_local(http: aiohttp.ClientSession, prompt: str, max_tokens: int) -> tuple[float, str]:
    t0 = time.perf_counter()
    async with http.post(
        f"{LOCAL_URL}/v1/chat/completions",
        json={
            "model": "qwen3-8b-instruct",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        },
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return time.perf_counter() - t0, data["choices"][0]["message"]["content"]


async def ask_openrouter(
    http: aiohttp.ClientSession, prompt: str, max_tokens: int, key: str
) -> tuple[float, str]:
    t0 = time.perf_counter()
    async with http.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        },
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return time.perf_counter() - t0, data["choices"][0]["message"]["content"]


async def main(runs: int, max_tokens: int) -> None:
    key = os.getenv("OPENROUTER_API_KEY", "")
    timeout = aiohttp.ClientTimeout(total=300)
    rows: list[dict[str, Any]] = []

    async with aiohttp.ClientSession(timeout=timeout) as http:
        for run in range(runs):
            for name, prompt in PROMPTS:
                row: dict[str, Any] = {"run": run + 1, "prompt": name}

                try:
                    dt, ans = await ask_local(http, prompt, max_tokens)
                    row["local_s"], row["local_checks"] = (
                        round(dt, 2),
                        score_answer(ans, name.startswith("domain")),
                    )
                    row["local_preview"] = ans[:90].replace("\n", " ")
                except Exception as exc:
                    row["local_s"], row["local_checks"] = (
                        None,
                        {"passed": False, "error": repr(exc)[:80]},
                    )

                if key:
                    try:
                        dt, ans = await ask_openrouter(http, prompt, max_tokens, key)
                        row["or_s"], row["or_checks"] = (
                            round(dt, 2),
                            score_answer(ans, name.startswith("domain")),
                        )
                        row["or_preview"] = ans[:90].replace("\n", " ")
                    except Exception as exc:
                        row["or_s"], row["or_checks"] = (
                            None,
                            {"passed": False, "error": repr(exc)[:80]},
                        )
                rows.append(row)
                print(json.dumps(row), flush=True)

    # ── verdict ──────────────────────────────────────────────────────────
    local_pass = [r for r in rows if r.get("local_checks", {}).get("passed")]
    or_rows = [r for r in rows if "or_checks" in r]
    or_pass = [r for r in or_rows if r.get("or_checks", {}).get("passed")]
    local_lat = [r["local_s"] for r in local_pass] or [0]
    or_lat = [r["or_s"] for r in or_pass] or [0]

    print("\n═══ SHADOW TEST VERDICT ═══")
    print(
        f"local :8009 : {len(local_pass)}/{len(rows)} passed | median {statistics.median(local_lat):.2f}s"
    )
    if key:
        print(
            f"openrouter  : {len(or_pass)}/{len(or_rows)} passed | median {statistics.median(or_lat):.2f}s"
        )
    local_rate = len(local_pass) / len(rows)
    if local_rate >= 0.9:
        print("RECOMMENDATION: local passes quality bar — cutover APPROVED for community chat.")
    elif local_rate >= 0.7:
        print("RECOMMENDATION: borderline — review failed cases before cutover.")
    else:
        print("RECOMMENDATION: DO NOT CUT OVER — local failing quality bar.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=120)
    args = ap.parse_args()
    asyncio.run(main(args.runs, args.max_tokens))
