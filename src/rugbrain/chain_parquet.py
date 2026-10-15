"""DuckDB analytics over chain-indexer parquet snapshots on Backblaze B2.

The chain-indexer (RugMunchMedia/chain-indexer) writes hourly parquet
snapshots of on-chain activity (ERC-20 transfers, DEX swaps) to B2:

    b2-crypt:chain-data/parquet/{chain}/{YYYY}/{MM}/{DD}/{HH}/
        {chain}_{YYYY-MM-DD}.parquet        # transfers/events
        {chain}_dex_{YYYY-MM-DD}.parquet    # DEX swaps

This is a *second* parquet data source for the RAG alongside the RAG's
own backup snapshots (``parquet_analytics.py`` → ``snapshots``
view). It gives callers historical chain data: transfers, DEX events,
whale activity — queryable with the same read-only SQL guard.

**Access path: rclone download + local DuckDB.** The ``b2-crypt`` remote
is an rclone *crypt* remote (content-encrypted with a passphrase), so
DuckDB's httpfs S3 reader cannot read the objects directly. We therefore
download the requested chain/date/hour prefix through ``rclone`` (which
decrypts on the fly) into a temp dir and run DuckDB over the local
parquet files — mirroring the download-fallback branch of
``parquet_analytics.py``.

Security: the endpoint is behind API-key auth, and
``validate_read_only_sql`` (shared with the permanence path) rejects
anything except read-only SELECT/WITH/DESCRIBE/SHOW statements. rclone
credentials come from the rclone config file / env, never from callers.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from rugbrain.parquet_analytics import (
    MAX_RESULTS,
    _connect_duckdb,
    _parquet_view_sql,
    json_safe_row,
    validate_read_only_sql,
)

logger = logging.getLogger(__name__)

# rclone remote + bucket for chain-indexer parquet (rclone crypt remote).
DEFAULT_CHAIN_REMOTE = os.getenv("B2_CHAIN_DATA_REMOTE", "b2-crypt")
DEFAULT_CHAIN_BUCKET = os.getenv("B2_CHAIN_DATA_BUCKET", "chain-data")
DEFAULT_CHAIN_PREFIX = os.getenv("B2_CHAIN_DATA_PREFIX", "parquet")

# rclone config path — same convention as scripts/backup_qdrant.py.
RCLONE_CONF = os.getenv("RCLONE_CONF", "~/.config/rclone/rclone.conf")

# Chain names known to the indexer (used for glob construction + intent).
KNOWN_CHAINS = [
    "arbitrum",
    "avalanche",
    "base",
    "bsc",
    "celo",
    "ethereum",
    "fantom",
    "gnosis",
    "linea",
    "mantle",
    "optimism",
    "polygon",
    "scroll",
    "solana",
    "tron",
    "zksync",
]

# Natural-language chain aliases → canonical indexer chain id.
CHAIN_ALIASES: dict[str, str] = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "bsc",
    "binance": "bsc",
    "bnb": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "solana": "solana",
    "sol": "solana",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "scroll": "scroll",
    "linea": "linea",
    "mantle": "mantle",
    "optimism": "optimism",
    "op": "optimism",
    "zksync": "zksync",
    "zksync era": "zksync",
    "tron": "tron",
    "tron network": "tron",
    "celo": "celo",
    "fantom": "fantom",
    "ftm": "fantom",
}

# Keywords that indicate a chain-data (structured on-chain) question.
_CHAIN_DATA_INTENT = re.compile(
    r"\b(transfer|transfers|swap|swaps|swapped|dex|whale|deploy|deployed|deployment|"
    r"deployments|mint|minted|burn|burned|liquidity|recent|yesterday|activity|"
    r"block_timestamp|tx_hash)\b",
    re.IGNORECASE,
)

# Zero address — token creation looks like a transfer FROM 0x0 (mint).
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


# ── Date helpers ──────────────────────────────────────────────────────


def _today_utc() -> date:
    return datetime.now(UTC).date()


def resolve_date(value: str | None) -> str | None:
    """Return ``YYYY-MM-DD`` for a date word or ISO date (None if unknown)."""
    if not value:
        return None
    v = value.strip().lower()
    if v in ("yesterday",):
        return (_today_utc() - timedelta(days=1)).isoformat()
    if v in ("today",):
        return _today_utc().isoformat()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", v)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3])).date().isoformat()
        except ValueError:
            return None
    # Also accept 20260803 / 2026/08/03 compact forms
    m = re.match(r"^(\d{4})[\/-]?(\d{2})[\/-]?(\d{2})$", v)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3])).date().isoformat()
        except ValueError:
            return None
    return None


def resolve_chain(value: str | None) -> str | None:
    """Map a chain alias to the canonical indexer chain id (None if unknown)."""
    if not value:
        return None
    return CHAIN_ALIASES.get(value.strip().lower())


# ── Glob / path construction ──────────────────────────────────────────


def chain_prefix(chain: str, date: str | None = None, hour: str | None = None) -> str:
    """Return the rclone path prefix for a chain/date/hour.

    ``b2-crypt:chain-data/parquet/{chain}/{YYYY}/{MM}/{DD}/{HH}`` — the
    hour directory is optional (omit to copy a whole day).
    """
    parts = [DEFAULT_CHAIN_PREFIX, chain]
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            d = None
        if d:
            parts += [str(d.year), f"{d.month:02d}", f"{d.day:02d}"]
            if hour:
                parts.append(hour.zfill(2))
    return f"{DEFAULT_CHAIN_REMOTE}:{DEFAULT_CHAIN_BUCKET}/{'/'.join(parts)}"


# ── rclone download ───────────────────────────────────────────────────


def _rclone_env() -> dict[str, str]:
    """Build the rclone environment (config path, B2 creds if set)."""
    env = dict(os.environ)
    conf = os.path.expanduser(RCLONE_CONF)
    if Path(conf).exists():
        env["RCLONE_CONFIG"] = conf
    return env


def _rclone_copy(remote_path: str, dest_dir: Path) -> int:
    """Download+decrypt ``remote_path`` into ``dest_dir``; return file count."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rclone",
        "copy",
        remote_path,
        str(dest_dir),
        "--include",
        "*.parquet",
        "--transfers",
        "4",
        "--no-update-modtime",
    ]
    logger.info("rclone_copy src=%s", remote_path)
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env=_rclone_env(),
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "rclone binary not found — chain-data parquet is rclone-crypt encrypted "
            "and requires rclone to download+decrypt"
        ) from None
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"rclone copy timed out for {remote_path}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"rclone copy failed for {remote_path}: {proc.stderr.strip()[:400]}")
    n = len(_find_parquet_files(dest_dir))
    if n == 0:
        raise RuntimeError(f"rclone copy returned no parquet files for {remote_path}")
    logger.info("rclone_copy_done files=%d", n)
    return n


def _find_parquet_files(dest_dir: Path) -> list[Path]:
    """Recursively find parquet files under *dest_dir* (rclone keeps subdirs)."""
    return sorted(p for p in dest_dir.rglob("*.parquet") if p.is_file())


# ── Query runner ──────────────────────────────────────────────────────


def _file_kind(path: Path) -> str:
    """Classify a parquet file as ``events`` or ``dex`` by filename."""
    name = path.name
    if "_dex_" in name:
        return "dex"
    return "events"


def _create_chain_views(con: Any, files: list[Path]) -> list[str]:
    """Create ``chain_events`` / ``chain_dex`` views over *files*.

    Returns the list of created view names. Views are only created when
    at least one matching file is present, so callers can query
    ``chain_events`` (transfers) and/or ``chain_dex`` (DEX swaps)
    depending on what the indexer wrote for the requested day.
    """
    events = [str(p) for p in files if _file_kind(p) == "events"]
    dex = [str(p) for p in files if _file_kind(p) == "dex"]
    created: list[str] = []
    if events:
        # Paths come from the local FS (rclone-downloaded B2 prefix filtered
        # to *.parquet, or internal test overrides) — never from the caller's
        # SQL. _sql_list quote-escapes each path; see _parquet_view_sql for
        # why the DDL inlines literals instead of using prepared parameters.
        con.execute(_parquet_view_sql("chain_events", _sql_list(events)))
        created.append("chain_events")
    if dex:
        con.execute(_parquet_view_sql("chain_dex", _sql_list(dex)))
        created.append("chain_dex")
    return created


def _sql_list(paths: list[str]) -> str:
    """Build a DuckDB literal array of file paths (SQL-quoted)."""
    quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in paths)
    return f"[{quoted}]"


def run_chain_query(
    query: str,
    chain: str | None = None,
    date: str | None = None,
    hour: str | None = None,
    limit: int = MAX_RESULTS,
    glob_override: str | None = None,
    files_override: list[Path] | None = None,
) -> dict[str, Any]:
    """Execute read-only *query* over indexer chain-data parquet.

    Two data paths:

    1. **Local / tests** — ``glob_override`` (or ``files_override``)
       bypasses B2 entirely: pass a local glob like
       ``/tmp/chain/*.parquet`` or an explicit file list.
    2. **B2 via rclone** — downloads the ``{chain}/{date}/{hour}`` prefix
       through ``rclone`` (decrypts the crypt remote) into a temp dir,
       then queries the local files.

    Exposes the ``chain_events`` (transfers) and ``chain_dex`` (DEX
    swaps) views. Returns ``{"query", "source", "chain", "date", "hour",
    "columns", "rows", "row_count", "truncated"}``.
    """
    validate_read_only_sql(query)
    con = _connect_duckdb()
    tmp_holder: Any = None
    try:
        if files_override is not None:
            files = files_override
            source = "local"
        elif glob_override is not None:
            files = sorted(Path(glob_override).parent.glob(Path(glob_override).name))
            source = "local"
        else:
            if not chain:
                raise ValueError("chain is required when querying B2 chain-data")
            tmp_holder = tempfile.TemporaryDirectory(prefix="rag-chain-")
            remote = chain_prefix(chain, date, hour)
            _rclone_copy(remote, Path(tmp_holder.name))
            files = _find_parquet_files(Path(tmp_holder.name))
            source = "b2-rclone"

        if not files:
            raise RuntimeError(f"no chain parquet files found for chain={chain} date={date}")
        views = _create_chain_views(con, files)
        if not views:
            raise RuntimeError("no queryable chain parquet views created")
        return _fetch_rows(con, query, limit, source, chain, date, hour, files)
    finally:
        if tmp_holder is not None:
            tmp_holder.cleanup()
        with contextlib.suppress(Exception):
            con.close()


def _fetch_rows(
    con: Any,
    query: str,
    limit: int,
    source: str,
    chain: str | None,
    date: str | None,
    hour: str | None,
    files: list[Path],
) -> dict[str, Any]:
    cur = con.execute(query)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows: list[list[Any]] = []
    truncated = False
    for row in cur.fetchmany(limit + 1):
        if len(rows) >= limit:
            truncated = True
            break
        rows.append(json_safe_row(row))
    return {
        "query": query,
        "source": source,
        "chain": chain,
        "date": date,
        "hour": hour,
        "files": [str(p) for p in files],
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "limit": limit,
    }


# ── Natural-language intent helpers (search fallback) ─────────────────


def detect_chain_data_intent(query: str) -> bool:
    """Whether a natural-language query looks like an on-chain data question."""
    return bool(_CHAIN_DATA_INTENT.search(query))


def extract_chain_from_query(query: str) -> str | None:
    """Extract the first canonical chain id mentioned in a query."""
    low = query.lower()
    for alias, chain in sorted(CHAIN_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return chain
    return None


def extract_date_from_query(query: str) -> str | None:
    """Extract a date word / ISO date from a query."""
    low = query.lower()
    if "yesterday" in low:
        return resolve_date("yesterday")
    if "today" in low:
        return resolve_date("today")
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", query)
    if m:
        return resolve_date(f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    return None


def _date_clause(date: str) -> str:
    """Range predicate for the timestamptz ``block_timestamp`` column.

    Compares ``epoch_ms(block_timestamp)`` against integer epoch bounds
    instead of using timestamptz literals: DuckDB's timestamptz literal
    handling requires the optional ``pytz`` module (not installed in the
    RAG image), while epoch-integer comparisons are timezone-safe and
    dependency-free.
    """
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return ""
    start = int(d.replace(tzinfo=UTC).timestamp() * 1000)
    end = int((d + timedelta(days=1)).replace(tzinfo=UTC).timestamp() * 1000)
    return f"epoch_ms(block_timestamp) >= {start} AND epoch_ms(block_timestamp) < {end}"


def build_intent_sql(query: str, chain: str | None, date: str | None, limit: int) -> str:
    """Build a safe read-only SQL statement for a chain-data search intent.

    Returns a SELECT against the ``chain_events`` / ``chain_dex`` views
    that reflects the question: transfers, DEX/whale activity, or token
    deployment (mint from the zero address).
    """
    low = query.lower()
    limit = max(1, min(int(limit), 100))

    where: list[str] = []
    if chain:
        where.append(f"chain = '{chain}'")
    date_clause = _date_clause(date) if date else ""
    if date_clause:
        where.append(date_clause)

    clause = "WHERE " + " AND ".join(where) if where else ""

    if any(w in low for w in ("swap", "swaps", "swapped", "dex", "whale", "liquidity")):
        cols = "type, chain, block_timestamp, tx_hash, dex, version, token0, token1, whale"
        return f"SELECT {cols} FROM chain_dex {clause} ORDER BY block_timestamp DESC LIMIT {limit}"  # noqa: S608  # nosec B608

    if any(w in low for w in ("deploy", "deployed", "deployment", "created", "launch")):
        cols = (
            "chain, block_timestamp, tx_hash, from_address, to_address, "
            "token_address, token_symbol, value"
        )
        zero = ZERO_ADDRESS
        base = "FROM chain_events"
        if where:
            base += f" WHERE {' AND '.join(where)} AND from_address = '{zero}'"
        else:
            base += f" WHERE from_address = '{zero}'"
        return f"SELECT {cols} {base} ORDER BY block_timestamp DESC LIMIT {limit}"

    # Default: recent transfers.
    cols = (
        "chain, block_timestamp, tx_hash, from_address, to_address, "
        "token_address, token_symbol, value"
    )
    return f"SELECT {cols} FROM chain_events {clause} ORDER BY block_timestamp DESC LIMIT {limit}"  # noqa: S608  # nosec B608
