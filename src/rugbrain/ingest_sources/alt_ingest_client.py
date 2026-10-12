"""Second-account Telethon client for the @cryptorugmunch channel alt-ingest.

Separate from ``rugbrain.ingest_sources.tg_client`` on purpose: that module
resolves the PRIMARY ingester's session, and sharing one MTProto session file
between two running processes gets the session killed by Telegram. The alt-ingest
account is the RM Insight (scraper) account, which egresses through its own
proxy so the two accounts never look co-located.

Credentials (gopass ``rmi/telegram-scraper/*``, projected into THESE env-var
names — never the bot token, and never the primary ingester's ``TELEGRAM_API_ID``
so the two accounts can never be confused):

    RAG_TG_ALT_INGEST_API_ID / RAG_TG_ALT_INGEST_API_HASH    <- gopass rmi/telegram-scraper/api_id|api_hash
    RAG_TG_ALT_INGEST_SESSION_STRING   (StringSession — preferred) or
    RAG_TG_ALT_INGEST_SESSION_FILE     (path to a .session file)
    RAG_TG_ALT_INGEST_PROXY_HOST / RAG_TG_ALT_INGEST_PROXY_PORT
    RAG_TG_ALT_INGEST_PROXY_USER / RAG_TG_ALT_INGEST_PROXY_PASS
    RAG_TG_ALT_INGEST_PROXY_TYPE       (http default | socks5)

Also supported: /srv/secrets/rmi-insight-tg-ingest.env (sourced by deployment)
"""

from __future__ import annotations

import os
from typing import Any

__all__ = [
    "AltIngestCredentialError",
    "alt_ingest_credentials_present",
    "build_alt_ingest_client",
]


class AltIngestCredentialError(RuntimeError):
    """The alt-ingest account's Telethon credentials or session are missing."""


_REQUIRED_ENV = ("RAG_TG_ALT_INGEST_API_ID", "RAG_TG_ALT_INGEST_API_HASH")
_OPTIONAL_SESSION_ENV = "RAG_TG_ALT_INGEST_SESSION_STRING"
_OPTIONAL_SESSION_FILE_ENV = "RAG_TG_ALT_INGEST_SESSION_FILE"


def alt_ingest_credentials_present() -> tuple[bool, list[str]]:
    """Report whether the alt-ingest account's credentials are complete.

    Returns ``(ok, missing_names)``. Names are env-var names only, never
    values, so a missing credential can be reported without revealing a secret.
    """
    missing: list[str] = []
    for name in _REQUIRED_ENV:
        if not os.getenv(name, "").strip():
            missing.append(name)
    if (
        not os.getenv(_OPTIONAL_SESSION_ENV, "").strip()
        and not os.getenv(_OPTIONAL_SESSION_FILE_ENV, "").strip()
    ):
        missing.append(_OPTIONAL_SESSION_ENV)
    return (not missing, missing)


def _proxy_from_env() -> tuple[Any, ...] | None:
    host = os.getenv("RAG_TG_ALT_INGEST_PROXY_HOST", "").strip()
    port = os.getenv("RAG_TG_ALT_INGEST_PROXY_PORT", "").strip()
    if not host or not port:
        return None
    try:
        p = int(port)
    except ValueError as err:
        raise AltIngestCredentialError(
            f"RAG_TG_ALT_INGEST_PROXY_PORT is not an integer: {port!r}"
        ) from err
    kind = os.getenv("RAG_TG_ALT_INGEST_PROXY_TYPE", "http").strip() or "http"
    if kind == "socks5":
        return (kind, host, p)
    return (
        kind,
        host,
        p,
        True,  # rdns
        os.getenv("RAG_TG_ALT_INGEST_PROXY_USER", "").strip(),
        os.getenv("RAG_TG_ALT_INGEST_PROXY_PASS", ""),
    )


def build_alt_ingest_client() -> Any:
    """Construct the alt-ingest account's Telethon ``TelegramClient``.

    Raises :class:`AltIngestCredentialError` when ``RAG_TG_ALT_INGEST_API_ID`` /
    ``RAG_TG_ALT_INGEST_API_HASH`` are absent — it never falls back to the bot
    token.
    """
    # Local import: Telethon is an ingest-time dependency, not a hard import for
    # every consumer of this package, and keeps the module importable in tests.
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = os.getenv("RAG_TG_ALT_INGEST_API_ID", "").strip()
    api_hash = os.getenv("RAG_TG_ALT_INGEST_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise AltIngestCredentialError(
            "RAG_TG_ALT_INGEST_API_ID / RAG_TG_ALT_INGEST_API_HASH not set (gopass rmi/telegram-scraper/api_id|api_hash)"
        )
    session_string = os.getenv(_OPTIONAL_SESSION_ENV, "").strip()
    session: Any = (
        StringSession(session_string)
        if session_string
        else os.getenv(_OPTIONAL_SESSION_FILE_ENV, "/var/lib/rugbrain/tg_alt_ingest.session")
    )
    proxy = _proxy_from_env()
    client_kwargs: dict[str, Any] = {"proxy": proxy} if proxy is not None else {}
    return TelegramClient(session, int(api_id), api_hash, **client_kwargs)
