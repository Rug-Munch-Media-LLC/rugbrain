"""Session-cookie loading for x.com.

Cookie *values* never leave this module: nothing here logs, formats or
returns a value into a string that could reach a log line. Only cookie
*names* are ever described.
"""

from __future__ import annotations

import json
import os
from typing import Any

from rugbrain.xsession import config

REQUIRED = ("auth_token", "ct0")


class CookieError(RuntimeError):
    """The cookie jar is missing, unreadable, or lacks a required cookie."""


def _pick(cookies: dict[str, str], name: str) -> str:
    value = cookies.get(name) or ""
    if not value:
        raise CookieError(f"missing cookie '{name}' in {config.COOKIE_PATH}")
    return value


def load_session_cookies(path: str | None = None) -> dict[str, str]:
    """Primary entry point: read the Firefox cookie export for x.com.

    The export is a list of ``{domain, name, value, ...}`` objects. Only the
    x.com / twitter.com entries are considered, and the most specific domain
    wins when a name appears twice.
    """
    target = path or config.COOKIE_PATH
    try:
        with open(target, encoding="utf-8") as handle:
            raw: Any = json.load(handle)
    except OSError as exc:
        raise CookieError(f"cannot read cookie jar: {exc.strerror}") from exc
    except ValueError as exc:
        raise CookieError("cookie jar is not valid JSON") from exc

    items = raw if isinstance(raw, list) else raw.get("cookies", [])
    picked: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "")
        name = str(item.get("name") or "")
        value = item.get("value")
        if domain not in config.COOKIE_DOMAINS or not name or not value:
            continue
        # Prefer the deepest domain (.x.com over .twitter.com) for the same name.
        picked[name] = str(value)

    for name in REQUIRED:
        _pick(picked, name)
    return picked


def cookie_header(cookies: dict[str, str]) -> str:
    """Minimal, sufficient Cookie header — only what the GraphQL lane needs."""
    return f"auth_token={_pick(cookies, 'auth_token')}; ct0={_pick(cookies, 'ct0')}"


def describe(cookies: dict[str, str]) -> dict[str, Any]:
    """Loggable summary: names + lengths only, never values."""
    return {
        "cookies": sorted(cookies),
        "auth_token_len": len(cookies.get("auth_token") or ""),
        "ct0_len": len(cookies.get("ct0") or ""),
        "authenticated_user": "present" if cookies.get("twid") else "absent",
        "mode": oct(os.stat(config.COOKIE_PATH).st_mode & 0o777),
    }
