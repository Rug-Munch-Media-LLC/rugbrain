"""Telethon client construction, as a seam the ingesters share.

Extracted from `telegram_userbot.py` so that adding capability here does not
grow the ingester, and so a second account can be served by the same code.

The proxy is the important part. The primary account connects directly from its
host; the secondary (RM Insight) account must egress through a different IP so
Telegram does not see two accounts from one origin and link or ban them. That
is why the proxy is read from the environment rather than hardcoded: each
account gets its own env file, and the same module serves both.

Env (all optional; no proxy = direct connection):

    TELEGRAM_PROXY_HOST, TELEGRAM_PROXY_PORT   — required to enable
    TELEGRAM_PROXY_USER, TELEGRAM_PROXY_PASS   — optional basic auth
    TELEGRAM_PROXY_TYPE                       — http (default) | socks5

Session and credentials:

    RAG_TG_SESSION_FILE   — path to the .session file
    TELEGRAM_API_ID, TELEGRAM_API_HASH
    RAG_TG_SESSION_STRING — alternative to a session file
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_PROXY_TYPE = "http"


class TelegramConfigError(RuntimeError):
    """Credentials or session material required by Telethon is missing."""


def proxy_from_env() -> tuple[Any, ...] | None:
    """Build a Telethon proxy tuple from the environment, or None for direct.

    Returns a half-open ``(connection_type, host, port, rdns, username,
    password)`` tuple, which is the shape Telethon expects. SOCKS5 is
    supported because Tor egress is a common substitute for a purchased
    residential proxy.
    """
    host = os.getenv("TELEGRAM_PROXY_HOST", "").strip()
    if not host:
        return None
    raw_port = os.getenv("TELEGRAM_PROXY_PORT", "").strip()
    if not raw_port:
        raise TelegramConfigError("TELEGRAM_PROXY_HOST set without TELEGRAM_PROXY_PORT")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise TelegramConfigError(f"TELEGRAM_PROXY_PORT is not an integer: {raw_port!r}") from exc
    kind = os.getenv("TELEGRAM_PROXY_TYPE", DEFAULT_PROXY_TYPE).strip() or DEFAULT_PROXY_TYPE
    user = os.getenv("TELEGRAM_PROXY_USER", "").strip()
    password = os.getenv("TELEGRAM_PROXY_PASS", "")
    # SOCKS5 is a datagram protocol; the rdns flag is meaningless and Telethon
    # rejects it for socks. Proxy credentials over Tor would be meaningless too.
    if kind == "socks5":
        return (kind, host, port)
    return (kind, host, port, True, user, password)


def build_client() -> Any:
    """Construct a connected-ready Telethon client from the environment."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise TelegramConfigError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH not set (gopass [INTERNAL_SECRET_PATH])"
        )
    session_string = os.getenv("RAG_TG_SESSION_STRING", "").strip()
    session = (
        StringSession(session_string)
        if session_string
        else os.getenv("RAG_TG_SESSION_FILE", "/var/lib/rugbrain/tg_ingest.session")
    )
    proxy = proxy_from_env()
    if proxy is not None:
        return TelegramClient(session, api_id, api_hash, proxy=proxy)
    return TelegramClient(session, api_id, api_hash)
