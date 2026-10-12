"""The pinned channel list the alt-ingest account sweeps — data only, no logic.

This is a PINNED COPY of ``_DEFAULT_CHANNELS`` in the @cryptorugmunch bot
(``src/rugmunchbot/channels/telegram_news/_sources/_config.py``), so the
RugBrain alt-ingest sweeps the exact same high-signal public crypto-news channels
the bot follows. The bot's list is the single source of truth: when the operator
changes it there, the diff lands here.

It lives in its own module because the list is ~110 lines of pure data — the
200-line-per-file cap is not negotiable, and a data list must not crowd out
code.

IMPORTANT: This is an ALT-ACCOUNT INGEST (read-only consumer). The MAIN account
(@cryptorugmunch bot) does the posting — this module must NEVER post.
"""

from __future__ import annotations

from typing import Final

#: Pinned copy of the bot's ``RMI_TG_NEWS_CHANNELS`` fallback list, in the bot's
#: own order so a diff against the bot's config is line-for-line.
ALT_INGEST_CHANNELS: Final[tuple[str, ...]] = (
    # ── News outlets ─────────────────────────────────────────────────────
    "cointelegraph",
    "coindesk",
    "cryptoslate",
    "WatcherGuru",
    "bitcoinmagazine",
    "PANewsOfficial",
    "cryptopotato",
    "coingape",
    "U_Today",
    "newsbtc",
    "dailyhodl",
    "zycrypto",
    "coinpedia",
    "cryptobriefing",
    "coinjournal",
    "thebitcoinnews",
    "altcoinbuzz",
    "AltcoinToday",
    "CoinMarketCap",
    "CoinGecko",
    "bybit_announcements",
    "coincodex",
    "cryptonewswire",
    # ── Security / exploit alerts ────────────────────────────────────────
    "CertiKAlert",
    "PeckShieldAlert",
    "rektnews",
    "ScamSniffer",
    "zachxbt",
    "BlockSecTeam",
    "halborn",
    "solidityfinance",
    "Hacken",
    "verichains",
    "trmlabs",
    "hypernative",
    "Cyvers",
    "AnciliaInc",
    "NeptuneMutual",
    "immunefi",
    # ── On-chain / alpha / whale tracking ────────────────────────────────
    "lookonchain",
    "spotonchain",
    "whale_alert",
    "OnChainWizard",
    "glassnode",
    "cryptowhaletrades",
    "intotheblock",
    "SantimentFeed",
    "CoinGlass",
    "Coinalyze",
    "defillama",
    "MessariCrypto",
    "tokenterminal",
    # ── DeFi / protocol channels ─────────────────────────────────────────
    "arbitrum",
    "avax",
    "zksync",
    "starknet",
    "aptos",
    "lidofinance",
    "chainlink",
    "Aave",
    "ethereum",
    "optimism",
    "polygon",
    "bnbchain",
    "NEARProtocol",
    "cosmos",
    "polkadot",
    "celestia",
    "avalancheavax",
    "jupiterexchange",
    "raydiumprotocol",
    "pancakeswap",
    "CurveFinance",
    "balancer",
    "yearnfinance",
    "convexfinance",
    # ── Research & macro ─────────────────────────────────────────────────
    "DelphiDigital",
    "alphanonce",
    "kryptocast",
    "BanklessHQ",
    "TheDeFiReport",
    "CryptoCred",
    # ── Trading & market analysis ────────────────────────────────────────
    "CryptoBubbles",
    "MaterialIndicators",
    "TradingShot",
    "crypto_traders",
    "fibonacci_trading",
    "u_today",
    # ── Verified from the @cryptorugmunch account's follows ──────────────
    "BlockchainReporter",
    "CryptoWorldNews",
    "Cryptocurrency_Inside",
    "cryptothedoggy",
    "FinancialWorldUpdates",
    "danny_crypton",
    "CryptoCapoTG",
    "shahlito",
    "spidersjournal",
    "Rektproof",
    "thepumpalert",
    "nansen_digest",
    "CryptoAlche",
    "CallAnalyser",
    "walloftrophies",
    "skeletonlounge",
    "rickburpbotnews",
    "blockchainmedia",
    "Coinspeaker",
    "coingeckonews",
    "CryptoNobler",
    "dimethyeth",
    "kamocalls",
    "fullfeed",
    "icodrops",
)


def resolve_channel_list(explicit: list[str] | None) -> list[str]:
    """Return the alt-ingest channels to sweep: explicit overrides or the full list."""
    if explicit:
        cleaned = [h.strip().lstrip("@") for h in explicit if h.strip()]
        if cleaned:
            return cleaned
    return list(ALT_INGEST_CHANNELS)


__all__ = ["ALT_INGEST_CHANNELS", "resolve_channel_list"]
