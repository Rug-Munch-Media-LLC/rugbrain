"""Configuration for the X/Twitter social-intel ingestion (single source of truth).

Ported from rugmunchbot ``tools/getxapi_config.py`` with two fixes:

* Base URL is ``https://api.getxapi.com`` — the legacy ``.../v1`` suffix 404s
  (live-verified 2026-09-18).
* Budget numbers reflect the live account: ``balance_total`` $4.65 ceiling,
  hard daily cap 150 calls, plan credits expiring 2026-10-02.

Key resolution: env ``GETXAPI_KEY`` → key file ``$RMI_SECRETS_DIR/getxapi_api_key``.
Never logged, never in git.
"""

from __future__ import annotations

import os
from typing import Any

# ── Core settings ────────────────────────────────────────────────────────
GETXAPI_BASE_URL: str = os.getenv("GETXAPI_BASE_URL", "https://api.getxapi.com").rstrip("/")
GETXAPI_COST_PER_CALL: float = float(os.getenv("GETXAPI_COST_PER_CALL", "0.001"))
#: Hard lifetime dollar ceiling — the live account balance (2026-09-18).
GETXAPI_TOTAL_BUDGET: float = float(os.getenv("GETXAPI_TOTAL_BUDGET", "4.65"))
#: Hard per-day call ceiling (calls). 150/day keeps the envelope alive past
#: the 2026-10-02 plan-credit expiry.
GETXAPI_DAILY_CALL_LIMIT: int = int(os.getenv("GETXAPI_DAILY_CALL_LIMIT", "150"))
#: Plan-credit expiry date (informational; logged at cycle start).
GETXAPI_CREDITS_EXPIRY: str = os.getenv("GETXAPI_CREDITS_EXPIRY", "2026-10-02")

GETXAPI_TIMEOUT_S: float = float(os.getenv("GETXAPI_TIMEOUT", "15"))
#: Redis cache TTLs (seconds) — a cached hit costs ZERO metered calls.
CACHE_TWEETS_S: float = float(os.getenv("XINTEL_CACHE_TWEETS_S", "900"))  # 15 min
CACHE_USER_INFO_S: float = float(os.getenv("XINTEL_CACHE_USER_INFO_S", "3600"))  # 1 h
CACHE_TRENDS_S: float = float(os.getenv("XINTEL_CACHE_TRENDS_S", "1800"))  # 30 min

# ── Pacing thresholds (softer than the hard caps; trigger Pry-only mode) ──
HOURLY_CALL_LIMIT: int = int(os.getenv("XINTEL_HOURLY_CALL_LIMIT", "50"))
PACING_DAILY_LIMIT: int = int(os.getenv("XINTEL_PACING_DAILY_LIMIT", "400"))
#: Follower threshold marking an account "high-value" (GetXAPI vs Pry).
HIGH_VALUE_FOLLOWERS: int = int(os.getenv("XINTEL_HIGH_VALUE_FOLLOWERS", "50000"))

# ── Cycle sizing ─────────────────────────────────────────────────────────
MAX_ACCOUNTS_PER_CYCLE: int = int(os.getenv("XINTEL_MAX_ACCOUNTS_PER_CYCLE", "3"))
TWEETS_PER_ACCOUNT: int = int(os.getenv("XINTEL_TWEETS_PER_ACCOUNT", "10"))
MIN_TWEET_CHARS: int = int(os.getenv("XINTEL_MIN_TWEET_CHARS", "8"))
RAG_COLLECTION: str = os.getenv("XINTEL_RAG_COLLECTION", "news_v2")

# ── Monitored accounts ───────────────────────────────────────────────────
_TIER1_RAW = os.getenv(
    "XINTEL_TIER1_ACCOUNTS",
    "zachxbt,peckshield,blockaid,certikalert,slowmist,beosin,"
    "chainalysis,elliptic,trmlabs,hashex,pwnfoo,angularrobot,samczsun,"
    "0xfoobar,hacxyk,coindesk,cointelegraph,thedefiscient,banklesssociety,"
    "cryptonews,watcherguru,wublockchain,blockworks_,unusual_whales,tier10k,"
    "flashbotdoteth,mevreful,eigenphi,0xrobs,hildobby",
)
_TIER2_RAW = os.getenv(
    "XINTEL_TIER2_ACCOUNTS",
    "cryptohornhorn,0xMert_,blknoiz06,cobie,ledgerstatus,CryptoKaleo,"
    "CryptoCapo_,rektcapital,inmortalcrypto,venturecoinist,cryptobirb,"
    "smartcontracter,ilblackcap,cryptoismiso,notsofast,puntium,"
    "therationalroot,cryptohamza,z3nch4n,cryptosdecrypto,theoneandonlyds,"
    "cryptomocho,h_s_zer0,cryptopunk6529,punk6529,wagiee,cryptogenet,"
    "cryptodraco,cryptobull,defi_mochi,defi_ant,defi_ox,tokenterminal,"
    "defillama,duneanalytics,nansen_ai,glassnode,santimentfeed,"
    "messaricrypto,intotheblock,coinmetrics,dapp_radar,etherscan,"
    "coingecko,coinmarketcap,dexscreener,geckoterminal,bubblemaps,"
    "arkhamintel,TheBlock__,decryptmedia,BanklessHQ,TheDefiant_io,"
    "solana,jupexfi,heliuslabs,jito_labs,pythnetwork,arbitrum,optimismPBC,"
    "base,scroll_zkp,zksync,0xPolygon,Mantle_Official,Blast_L2,SuiNetwork,"
    "Aptos,ton_blockchain,NEARProtocol,Flow_blockchain,CelestiaOrg,"
    "eigenlayer,openzeppelin,trailofbits,quantstamp,HackenClub,immunefi,"
    "halbornHQ,VitalikButerin,CZ_Binance,sassal0x,aeyakovenko,APompliano,"
    "TheCryptoLark,Nicholas_Merten,100trillionUSD,hasufl,jchervinsky,"
    "gakonst,trent_vanepps,nic__carter,Melt_Dem,CeterisPar1bus,danheld,"
    "TheCryptoDog,cryptocobain,zhusu,aantonop,Boxmining,IvanOnTech,muneeb",
)
_TIER3_4_RAW = os.getenv(
    "XINTEL_TIER3_4_KEYWORD_QUERIES",
    '"shill" OR "pump" OR "moon" OR "100x" crypto -is:retweet min_faves:100'
    '|"rug" OR "scam" OR "honeypot" crypto -is:retweet min_faves:50'
    '|"airdrop" OR "claim" OR "free" crypto -is:retweet min_faves:200'
    '|"launch" OR "presale" OR "IDO" crypto -is:retweet min_faves:50'
    '|"KOL" OR "influencer" OR "shiller" crypto -is:retweet min_faves:20'
    '|"guaranteed" OR "risk-free" crypto -is:retweet'
    '|"to the moon" OR "next bitcoin" -is:retweet min_faves:100'
    '|"get in early" OR "last chance" crypto -is:retweet min_faves:50',
)


def _handles(raw: str) -> tuple[str, ...]:
    return tuple(h.strip().lstrip("@") for h in raw.split(",") if h.strip())


def _queries(raw: str) -> tuple[str, ...]:
    return tuple(q.strip() for q in raw.split("|") if q.strip())


TIER1_ACCOUNTS: tuple[str, ...] = _handles(_TIER1_RAW)
TIER2_ACCOUNTS: tuple[str, ...] = _handles(_TIER2_RAW)
TIER3_4_KEYWORD_QUERIES: tuple[str, ...] = _queries(_TIER3_4_RAW)

# ── Key resolution ───────────────────────────────────────────────────────


def _key_file() -> str:
    return os.getenv("GETXAPI_KEY_FILE") or os.path.join(
        os.getenv("RMI_SECRETS_DIR", "/srv/secrets"), "getxapi_api_key"
    )


def resolve_api_key() -> str:
    """GetXAPI key: env ``GETXAPI_KEY`` → key file. Uncached, never logged."""
    env_key = os.getenv("GETXAPI_KEY", "").strip()
    if env_key:
        return env_key
    path = _key_file()
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    except (OSError, UnicodeDecodeError) as e:
        import logging

        logging.getLogger(__name__).warning("xintel:key_file_unreadable err=%s", e)
    return ""


def api_key_configured() -> bool:
    """True when a GetXAPI key is available (env or key file)."""
    return bool(resolve_api_key())


def as_dict() -> dict[str, Any]:
    """Config snapshot for logging / admin visibility (key redacted)."""
    return {
        "base_url": GETXAPI_BASE_URL,
        "key_configured": api_key_configured(),
        "total_budget": GETXAPI_TOTAL_BUDGET,
        "daily_call_limit": GETXAPI_DAILY_CALL_LIMIT,
        "cost_per_call": GETXAPI_COST_PER_CALL,
        "credits_expiry": GETXAPI_CREDITS_EXPIRY,
        "hourly_call_limit": HOURLY_CALL_LIMIT,
        "pacing_daily_limit": PACING_DAILY_LIMIT,
        "tier1_accounts": list(TIER1_ACCOUNTS),
        "tier2_accounts": list(TIER2_ACCOUNTS),
        "tier3_4_keyword_queries": list(TIER3_4_KEYWORD_QUERIES),
        "rag_collection": RAG_COLLECTION,
    }


__all__ = [
    "CACHE_TRENDS_S",
    "CACHE_TWEETS_S",
    "CACHE_USER_INFO_S",
    "GETXAPI_BASE_URL",
    "GETXAPI_COST_PER_CALL",
    "GETXAPI_CREDITS_EXPIRY",
    "GETXAPI_DAILY_CALL_LIMIT",
    "GETXAPI_TIMEOUT_S",
    "GETXAPI_TOTAL_BUDGET",
    "HIGH_VALUE_FOLLOWERS",
    "HOURLY_CALL_LIMIT",
    "MAX_ACCOUNTS_PER_CYCLE",
    "MIN_TWEET_CHARS",
    "PACING_DAILY_LIMIT",
    "RAG_COLLECTION",
    "TIER1_ACCOUNTS",
    "TIER2_ACCOUNTS",
    "TIER3_4_KEYWORD_QUERIES",
    "TWEETS_PER_ACCOUNT",
    "api_key_configured",
    "as_dict",
    "resolve_api_key",
]
