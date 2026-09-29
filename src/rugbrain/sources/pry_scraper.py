"""Self-hosted Pry scraper client.

Full rationale, the four ways callers previously mis-read Pry, and the auth
decision are in docs/sources/PRY.md. The short version: Pry is x402
pay-per-call by default and an unauthenticated call returns HTTP 402
"payment required: send 1000 USDC", which reads exactly like a dead service.
This module is the single free path, and it exists so nobody re-derives that.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

import httpx

log = logging.getLogger(__name__)

PRY_URL = os.getenv("PRY_URL", "http://127.0.0.1:8005").rstrip("/")

#: Seconds for one scrape. Pry escalates through 15 anti-bot tiers and a hard
#: failure costs 45s of wall clock on its own, so the client timeout must exceed
#: that or every hard-blocked site looks like a client error.
SCRAPE_TIMEOUT = float(os.getenv("PRY_SCRAPE_TIMEOUT", "90"))

#: Politeness. This host is scraping third-party sites; one caller at a time at
#: a fixed interval is the point, not a throughput knob.
DEFAULT_DELAY_SECONDS = float(os.getenv("PRY_DELAY_SECONDS", "1.0"))
DEFAULT_CONCURRENCY = int(os.getenv("PRY_CONCURRENCY", "2"))

#: Absolute path. Ruff S607 forbids a partial executable path, and the rule is
#: right: a bare "docker" resolves against PATH, so a stray shim earlier in PATH
#: would be executed with this code's privileges. The container is named, and
#: the binary is pinned, so both are unambiguous.
DOCKER = "/usr/bin/docker"

_resolved_key: str | None = None


class PryError(RuntimeError):
    """A scrape failed. ``stage`` says which layer, so the caller can act."""


def _key_from_container() -> str | None:
    """Read PRY_API_KEY from the running container.

    The key is in the container's environment, not in a tracked file, so a
    fresh checkout has no other way to obtain it. Returns None rather than
    raising so the caller gets one actionable message.
    """
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            # Absolute path: S607 is right that a bare "docker" resolves against
            # PATH, so a shim earlier in PATH would run with these privileges.
            #
            # Literal argv, no shell, nothing from a request or config file.
            # S603 is right in general; the alternative is not running docker.
            [DOCKER, "inspect", "pry", "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("pry_container_inspect_failed", extra={"error": str(exc)})
        return None
    for line in out.stdout.splitlines():
        if line.startswith("PRY_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None


def owner_key() -> str:
    """The owner key, from env or the container. Raises PryError if absent."""
    global _resolved_key
    if _resolved_key:
        return _resolved_key
    key = os.getenv("PRY_API_KEY", "").strip() or _key_from_container()
    if not key:
        raise PryError(
            "no PRY_API_KEY: set it in the environment, or start the `pry` "
            "container which carries it. Without it Pry returns HTTP 402 "
            "'payment required: send 1000 USDC ...', which is this whole problem."
        )
    _resolved_key = key
    return key


async def scrape(
    url: str,
    *,
    format: str = "markdown",
    wait_for: int = 3000,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Scrape one URL and return its text.

    Returns the rendered text. Raises PryError with a stage on failure, because
    Pry's own failure text is diagnostic and must not be flattened into
    "no content".

    Stages:
      unreachable   Pry itself did not answer
      payment       the 402, which means the owner key was not accepted
      blocked       Pry tried every anti-bot tier and gave up; ``detail`` from
                    the response names each tier
      empty         Pry succeeded but the page rendered no text
    """
    headers = {"Authorization": f"Bearer {owner_key()}", "Content-Type": "application/json"}
    payload = {"url": url, "format": format, "wait_for": wait_for}
    own = client is None
    http = client or httpx.AsyncClient(timeout=SCRAPE_TIMEOUT)
    try:
        response = await http.post(f"{PRY_URL}/v1/scrape", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise PryError("unreachable", f"cannot reach Pry at {PRY_URL}: {exc}") from exc
    finally:
        if own:
            await http.aclose()

    if response.status_code == 402:
        raise PryError(
            "payment",
            "Pry returned 402. The owner key was not accepted — check PRY_API_KEY.",
        )
    try:
        body: dict[str, Any] = response.json()
    except ValueError as exc:
        raise PryError("unreachable", f"Pry returned non-JSON: {response.text[:120]}") from exc

    # Pry reports hard-blocked sites as a typed error, and its `detail` names
    # every tier it tried. That text is the most useful thing it produces, so
    # it is surfaced rather than discarded.
    err_type = str(body.get("type", ""))
    if err_type.endswith("scrape_failed") or response.status_code >= 400:
        detail = body.get("detail") or body.get("message") or response.text[:200]
        raise PryError("blocked", f"Pry could not scrape {url}: {detail}")

    # The text is at data.markdown. Not at content, not at the top level. Two
    # earlier callers read the wrong key and concluded the scrape was empty.
    data = body.get("data") or {}
    text = data.get("markdown") or data.get("text") or data.get("html") or ""
    if not text.strip():
        raise PryError("empty", f"Pry succeeded but rendered no text for {url}")
    return str(text)


async def scrape_many(
    urls: list[str],
    *,
    concurrency: int | None = None,
    delay: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str | str]:
    """Scrape many URLs politely.

    Returns url -> text, or url -> a PryError message. Deliberately never
    raises for an individual URL: a sweep of 250 channels must record which ones
    failed and why rather than aborting on the first Cloudflare wall. The caller
    decides whether a low success rate is a problem.

    Concurrency defaults to 2 and the delay to 1s. Capacity is not the
    constraint on this host (the pry container idles at ~1.7% CPU and 510MB of a
    2GiB cap); being a good citizen to the sites being scraped is.
    """
    limit = max(1, concurrency or DEFAULT_CONCURRENCY)
    gap = DEFAULT_DELAY_SECONDS if delay is None else delay
    sem = asyncio.Semaphore(limit)
    results: dict[str, str | str] = {}
    http = client or httpx.AsyncClient(timeout=SCRAPE_TIMEOUT)

    async def one(target: str) -> tuple[str, str | str]:
        async with sem:
            try:
                return target, await scrape(target, client=http)
            except PryError as exc:
                return target, f"{exc.args[0]}: {exc.args[1]}"
            finally:
                if gap:
                    await asyncio.sleep(gap)

    try:
        for target, outcome in await asyncio.gather(*(one(u) for u in urls)):
            results[target] = outcome
    finally:
        if client is None:
            await http.aclose()
    return results


__all__ = [
    "PRY_URL",
    "PryError",
    "owner_key",
    "scrape",
    "scrape_many",
]
