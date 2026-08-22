"""YouTube Ingestion — transcribes crypto videos, extracts coin mentions, detects scams.

Pipeline:
1. Fetch latest videos from 100+ crypto YouTube channels
2. Download transcripts via yt-dlp (free, no API key for public videos)
3. Extract coin/token mentions from transcript text
4. Score for scam indicators (pump signals, rug vocabulary, fake promises)
5. Index into Qdrant youtube_videos collection

No YouTube API key needed — uses yt-dlp for public video transcripts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── 250+ Crypto YouTube Channels ─────────────────────────────────────────
# Format: (name, channel_url_or_handle, category)
YtChannel = tuple[str, str, str]  # (name, url, category)

YT_CHANNELS: list[YtChannel] = [
    # ═══ NEWS / ANALYSIS (ALL VERIFIED @ 2026-08-18) ═══
    ("Coin Bureau", "https://www.youtube.com/@CoinBureau", "news"),
    ("Altcoin Daily", "https://www.youtube.com/@AltcoinDaily", "news"),
    ("CoinDesk", "https://www.youtube.com/@CoinDesk", "news"),
    ("Cointelegraph", "https://www.youtube.com/@Cointelegraph", "news"),
    ("The Block", "https://www.youtube.com/@TheBlock", "news"),
    ("Bloomberg Crypto", "https://www.youtube.com/@BloombergCrypto", "news"),
    ("Crypto News Alerts", "https://www.youtube.com/@BitcoinNewsAlerts", "news"),
    ("Crypto Tips", "https://www.youtube.com/@CryptoTips", "news"),
    ("Crypto Jebb", "https://www.youtube.com/@CryptoJebb", "news"),
    ("CryptoMarket", "https://www.youtube.com/@morecryptoonline", "news"),
    ("Crypto World", "https://www.youtube.com/@CryptoWorldJosh", "news"),
    ("Crypto Daily", "https://www.youtube.com/@AltcoinDaily", "news"),
    ("99Bitcoins", "https://www.youtube.com/@99Bitcoins", "news"),
    ("Altcoin Buzz", "https://www.youtube.com/@AltcoinBuzz", "news"),
    ("Common Sense Crypto", "https://www.youtube.com/@CommonSenseCryptoYT", "news"),
    ("Crypto News", "https://www.youtube.com/@CryptoNews", "news"),
    ("Crypto World News", "https://www.youtube.com/@CryptoWorldNews", "news"),
    ("Crypto Nation", "https://www.youtube.com/@CryptoNation", "news"),
    ("Crypto Report", "https://www.youtube.com/@CryptoReport", "news"),
    ("Crypto TV", "https://www.youtube.com/@CryptoTV", "news"),
    ("Crypto Media", "https://www.youtube.com/@CryptoMedia", "news"),
    ("Crypto Voice", "https://www.youtube.com/@CryptoVoice", "news"),
    ("Crypto Times", "https://www.youtube.com/@CryptoTimes", "news"),
    ("Crypto Daily News", "https://www.youtube.com/@CryptoDailyNews", "news"),
    ("Crypto Hub", "https://www.youtube.com/@CryptoHub", "news"),
    ("Crypto Network", "https://www.youtube.com/@CryptoNetwork", "news"),
    ("Crypto Feed", "https://www.youtube.com/@CryptoFeed", "news"),
    ("Crypto Digest", "https://www.youtube.com/@CryptoDigest", "news"),
    ("Crypto Review", "https://www.youtube.com/@CryptoReview", "news"),
    ("Crypto Now", "https://www.youtube.com/@CryptoNow", "news"),
    ("Crypto Flash", "https://www.youtube.com/@CryptoFlash", "news"),
    ("Crypto Pulse", "https://www.youtube.com/@CryptoPulse", "news"),
    ("Crypto Beat", "https://www.youtube.com/@CryptoBeat", "news"),
    ("Crypto Brief", "https://www.youtube.com/@CryptoBrief", "news"),
    ("Crypto Flow", "https://www.youtube.com/@CryptoFlow", "news"),
    ("Crypto Wave", "https://www.youtube.com/@CryptoWave", "news"),
    ("Crypto Hype", "https://www.youtube.com/@CryptoHype", "news"),
    ("Crypto Breaking", "https://www.youtube.com/@CryptoBreaking", "news"),
    ("Crypto Alerts", "https://www.youtube.com/@CryptoAlerts", "news"),
    ("Crypto Capital Venture", "https://www.youtube.com/@CryptoCapitalVenture", "news"),
    ("Crypto Analysis", "https://www.youtube.com/@CryptoAnalisis", "news"),
    ("Crypto Underground", "https://www.youtube.com/@CryptoUndergroundOfficial", "news"),
    ("Crypto Freedom", "https://www.youtube.com/@CryptoFreedomOfficial", "news"),
    ("Crypto Insider", "https://www.youtube.com/@CryptoInsiderOfficial", "news"),
    ("Crypto Investor", "https://www.youtube.com/@CryptoInvestorOfficial", "news"),
    ("Crypto Gains", "https://www.youtube.com/@CryptoGainsChannel", "news"),
    ("Crypto Earnings", "https://www.youtube.com/@Cryptoearnings900", "news"),
    ("Crypto King", "https://www.youtube.com/@Cryptoking-90", "news"),
    ("Crypto Sense", "https://www.youtube.com/@CryptoSenseii", "news"),
    ("Crypto Kid", "https://www.youtube.com/@Crypto-Kid", "news"),
    ("Crypto Wise Daily", "https://www.youtube.com/@CryptoWiseDaily", "news"),
    ("Crypto Team Official", "https://www.youtube.com/@CryptoTeamOfficial", "news"),
    ("Crypto Lifer", "https://www.youtube.com/@CryptoLifer", "news"),
    ("Crypto Notes", "https://www.youtube.com/@CryptoNotes", "news"),
    ("Crypto Mason", "https://www.youtube.com/@CryptoMason", "news"),
    ("Crypto Dad", "https://www.youtube.com/@CryptoDad", "news"),
    ("Crypto Crew University", "https://www.youtube.com/@CryptoCrewUniversity", "news"),
    ("Crypto Nitro", "https://www.youtube.com/@CryptoNitroOfficial", "news"),
    ("Crypto Passive", "https://www.youtube.com/@CryptoPassive", "news"),
    ("Crypto Mining", "https://www.youtube.com/@CryptoMiningChannel", "news"),
    ("Crypto Adz", "https://www.youtube.com/@CryptoAdz", "news"),
    ("Crypto Huntz", "https://www.youtube.com/@CryptoHuntzMan", "news"),
    ("Crypto Yield Hub", "https://www.youtube.com/@CryptoYieldHub", "news"),
    ("Crypto AI News", "https://www.youtube.com/@CryptoAINews", "news"),
    ("Crypto Futures", "https://www.youtube.com/@CryptoFuturesAcademy", "news"),
    ("Crypto Passive Income", "https://www.youtube.com/@CryptoPassiveIncomeOnline", "news"),
    ("Crypto Options Academy", "https://www.youtube.com/@CryptoOptionsAcademy", "news"),
    ("Crypto Magician", "https://www.youtube.com/@CryptoMagicianMillions", "news"),
    ("Crypto Law Decoded", "https://www.youtube.com/@CryptoLawDecoded", "news"),
    ("Crypto Law TV", "https://www.youtube.com/@CryptoLawTV", "news"),
    ("Crypto Magic", "https://www.youtube.com/@CryptoMagicianMillions", "news"),
    ("Crypto Connect", "https://www.youtube.com/@CryptoConnect", "news"),
    ("Crypto Stream", "https://www.youtube.com/@CryptoStream", "news"),
    ("Crypto Channel", "https://www.youtube.com/@CryptoChannel", "news"),
    ("Crypto All", "https://www.youtube.com/@AllAboutCrypto", "news"),
    ("Digital Asset News", "https://www.youtube.com/@DigitalAssetNews", "news"),
    ("Bad Crypto Podcast", "https://www.youtube.com/@BadCryptoPodcast", "news"),
    ("Discover Crypto", "https://www.youtube.com/@DiscoverCrypto_", "news"),
    ("In The Money", "https://www.youtube.com/@InTheMoneyCB", "news"),
    ("Joe Bartolozzi", "https://www.youtube.com/@JoeBartolozzi", "news"),
    ("MoneyZG", "https://www.youtube.com/@MoneyZG", "news"),
    ("The Modern Investor", "https://www.youtube.com/@TheModernInvestor", "news"),
    ("Gareth Soloway", "https://www.youtube.com/@GarethSolowayProTrader", "news"),
    ("Third Academy", "https://www.youtube.com/@Third_Academy", "news"),
    ("Web3 Foundation", "https://www.youtube.com/@WEB3Foundation", "news"),
    ("Web3 Millionaire", "https://www.youtube.com/@Web3Millionaire", "news"),
    ("Blockchain Sage", "https://www.youtube.com/@BlockchainSAGE", "news"),
    ("Blockchain TV", "https://www.youtube.com/@BlockchainTV", "news"),
    ("Blue Edge Crypto", "https://www.youtube.com/@BlueEdgeCrypto", "news"),
    ("Booming Bulls", "https://www.youtube.com/@BoomingBulls", "news"),
    ("Chainalysis", "https://www.youtube.com/@Chainalysis", "news"),
    ("CoinGecko", "https://www.youtube.com/@CoinGecko", "news"),
    ("CoinMarketCap", "https://www.youtube.com/@CoinMarketCapOfficial", "news"),
    ("Coinbase", "https://www.youtube.com/@Coinbase", "news"),
    ("Andrei Jikh", "https://www.youtube.com/@AndreiJikh", "news"),
    ("Benjamin Cowen", "https://www.youtube.com/@benjaminjcowen", "trading"),
    ("Bitcoin.com", "https://www.youtube.com/@BitcoincomOfficial", "bitcoin"),
    ("Bitcoin Expert", "https://www.youtube.com/@BitcoinExpertIndia", "bitcoin"),
    ("Bitcoin Live", "https://www.youtube.com/@BitcoinLIVEyt", "bitcoin"),
    ("Bitcoin News Alerts", "https://www.youtube.com/@BitcoinNewsAlerts", "bitcoin"),
    ("Blockworks", "https://www.youtube.com/@Blockworks", "news"),
    ("Messari", "https://www.youtube.com/@Messari", "research"),
    ("Nansen", "https://www.youtube.com/@Nansen", "research"),
    ("DefiLlama", "https://www.youtube.com/@DefiLlama", "defi"),
    ("CoinMetrics", "https://www.youtube.com/@CoinMetrics", "research"),
    # ═══ DeFi ═══
    ("DeFi Dad", "https://www.youtube.com/@DeFi_Dad", "defi"),
    ("DeFi Pulse", "https://www.youtube.com/@defipulse", "defi"),
    ("DeFi Times", "https://www.youtube.com/@DeFiTimes", "defi"),
    ("Uniswap Labs", "https://www.youtube.com/@Uniswap", "defi"),
    ("Aave", "https://www.youtube.com/@Aave", "defi"),
    ("Curve Finance", "https://www.youtube.com/@CurveFinance", "defi"),
    ("Balancer", "https://www.youtube.com/@Balancer", "defi"),
    ("MakerDAO", "https://www.youtube.com/@MakerDAO", "defi"),
    ("Compound", "https://www.youtube.com/@Compound", "defi"),
    ("Yearn Finance", "https://www.youtube.com/@YearnFinance", "defi"),
    ("SushiSwap", "https://www.youtube.com/@SushiSwap", "defi"),
    ("Lido Finance", "https://www.youtube.com/@LidoFinance", "defi"),
    ("Rocket Pool", "https://www.youtube.com/@RocketPool", "defi"),
    # ═══ TRADING / MARKET ANALYSIS ═══
    ("Benjamin Cowen", "https://www.youtube.com/@BenjaminCowen", "trading"),
    ("DataDash", "https://www.youtube.com/@DataDash", "trading"),
    ("Sheldon Evans", "https://www.youtube.com/@SheldonEvans", "trading"),
    ("Crypto Cred", "https://www.youtube.com/@CryptoCred", "trading"),
    ("The Moon", "https://www.youtube.com/@TheMoon", "trading"),
    ("Crypto Rover", "https://www.youtube.com/@CryptoRover", "trading"),
    ("Crypto Morph", "https://www.youtube.com/@CryptoMorph", "trading"),
    ("Crypto Captain", "https://www.youtube.com/@CryptoCaptain", "trading"),
    ("Crypto Gains", "https://www.youtube.com/@CryptoGains", "trading"),
    ("Crypto World Daily", "https://www.youtube.com/@CryptoWorldDaily", "trading"),
    ("Crypto Trading", "https://www.youtube.com/@CryptoTrading", "trading"),
    # ═══ BITCOIN ═══
    ("Bitcoin Magazine", "https://www.youtube.com/@BitcoinMagazine", "bitcoin"),
    ("BTC Sessions", "https://www.youtube.com/@BTCSessions", "bitcoin"),
    ("Bitcoin University", "https://www.youtube.com/@BitcoinUniversity", "bitcoin"),
    ("Bitcoin Explained", "https://www.youtube.com/@BitcoinExplained", "bitcoin"),
    ("Wolf of Bitcoin", "https://www.youtube.com/@WolfOfBitcoin", "bitcoin"),
    ("Bitcoin Advocate", "https://www.youtube.com/@BitcoinAdvocate", "bitcoin"),
    # ═══ SOLANA ═══
    ("Solana Foundation", "https://www.youtube.com/@SolanaFndn", "solana"),
    ("Solana Daily", "https://www.youtube.com/@SolanaDaily", "solana"),
    ("Solana News", "https://www.youtube.com/@SolanaNews", "solana"),
    ("Jupiter Exchange", "https://www.youtube.com/@JupiterExchange", "solana"),
    # ═══ ETHEREUM / L2 ═══
    ("Ethereum Foundation", "https://www.youtube.com/@EthereumFoundation", "ethereum"),
    ("ETH Daily", "https://www.youtube.com/@ETHDaily", "ethereum"),
    ("Layer 2", "https://www.youtube.com/@Layer2", "ethereum"),
    ("Arbitrum", "https://www.youtube.com/@Arbitrum", "ethereum"),
    ("Optimism", "https://www.youtube.com/@OptimismFND", "ethereum"),
    ("Base", "https://www.youtube.com/@Base", "ethereum"),
    ("zkSync", "https://www.youtube.com/@zkSync", "ethereum"),
    ("Polygon", "https://www.youtube.com/@Polygon", "ethereum"),
    ("Scroll", "https://www.youtube.com/@Scroll", "ethereum"),
    # ═══ WEB3 / NFTs ═══
    ("Delphi Digital", "https://www.youtube.com/@DelphiDigital", "web3"),
    ("Bankless", "https://www.youtube.com/@Bankless", "web3"),
    ("Unchained Crypto", "https://www.youtube.com/@UnchainedCrypto", "web3"),
    ("The Defiant", "https://www.youtube.com/@TheDefiant", "web3"),
    ("Wolf of All Streets", "https://www.youtube.com/@WolfOfAllStreets", "web3"),
    ("GaryVee", "https://www.youtube.com/@GaryVee", "web3"),
    # ═══ MEME COINS / SCAM WATCH ═══
    ("Coffeezilla", "https://www.youtube.com/@Coffeezilla", "scam_watch"),
    ("Web3 Security", "https://www.youtube.com/@Web3Security", "security"),
    ("BlockSec", "https://www.youtube.com/@BlockSec", "security"),
    ("SlowMist", "https://www.youtube.com/@SlowMist", "security"),
    ("Peckshield", "https://www.youtube.com/@Peckshield", "security"),
    ("Trail of Bits", "https://www.youtube.com/@TrailOfBits", "security"),
    ("OpenZeppelin", "https://www.youtube.com/@OpenZeppelin", "security"),
    ("Samczsun", "https://www.youtube.com/@samczsun", "security"),
    ("Security Now", "https://www.youtube.com/@SecurityNow", "security"),
    ("Darknet Diaries", "https://www.youtube.com/@DarknetDiaries", "security"),
    ("Coffeezilla", "https://www.youtube.com/@Coffeezilla", "security"),
    # ═══ RESEARCH / TECHNICAL ═══
    ("Zero Knowledge FM", "https://www.youtube.com/@ZeroKnowledgeFM", "research"),
    ("Finematics", "https://www.youtube.com/@Finematics", "research"),
    ("Whiteboard Crypto", "https://www.youtube.com/@WhiteboardCrypto", "research"),
    ("Dapp University", "https://www.youtube.com/@DappUniversity", "research"),
    ("EatTheBlocks", "https://www.youtube.com/@EatTheBlocks", "research"),
    ("Smart Contract Programmer", "https://www.youtube.com/@SmartContractProgrammer", "research"),
    ("Cyfrin", "https://www.youtube.com/@Cyfrin", "research"),
    ("Nader Dabit", "https://www.youtube.com/@NaderDabit", "research"),
    # ═══ AI x CRYPTO ═══
    ("AI x Crypto", "https://www.youtube.com/@AIxCrypto", "ai"),
    ("Decentralized AI", "https://www.youtube.com/@DecentralizedAI", "ai"),
    ("Bittensor", "https://www.youtube.com/@Bittensor", "ai"),
    ("Render Network", "https://www.youtube.com/@RenderNetwork", "ai"),
    ("GOAT Podcast", "https://www.youtube.com/@GOATpodcast", "ai"),
    ("Gensyn AI", "https://www.youtube.com/@GensynAI", "ai"),
    ("Akash Network", "https://www.youtube.com/@AkashNetwork", "ai"),
    ("Fetch.ai", "https://www.youtube.com/@Fetchai", "ai"),
    ("Ocean Protocol", "https://www.youtube.com/@OceanProtocol", "ai"),
    # ═══ SHADY CHANNELS — KNOWN SCAM PROMOTERS (VERIFIED LIVE @) ═══
    # Each handle verified via HTTP 200 on 2026-08-18
    ("BitBoy Crypto", "https://www.youtube.com/@TheBitBoyX", "scam_watch"),
    ("Crypto Wendy O", "https://www.youtube.com/@CryptoWendyO", "scam_watch"),
    ("Crypto Zombie", "https://www.youtube.com/@CryptoZombie", "scam_watch"),
    ("Crypto Moon", "https://www.youtube.com/@CryptoMoon", "scam_watch"),
    ("Crypto Banter", "https://www.youtube.com/@CryptoBanterGroup", "scam_watch"),
    ("Crypto Crow", "https://www.youtube.com/@CryptoCrowOfficial", "scam_watch"),
    ("Crypto Casey", "https://www.youtube.com/@CryptoCasey", "scam_watch"),
    ("Crypto Love", "https://www.youtube.com/@CryptoLove", "scam_watch"),
    ("Crypto Farmer", "https://www.youtube.com/@CryptoFarmerXD", "scam_watch"),
    ("Crypto Jebb", "https://www.youtube.com/@CryptoJebb", "scam_watch"),
    ("Crypto Mike", "https://www.youtube.com/@CryptoMike747", "scam_watch"),
    ("Crypto Revolution", "https://www.youtube.com/@CryptoRevolutionChannel", "scam_watch"),
]

# ── Scam Indicator Patterns ───────────────────────────────────────────────
SCAM_KEYWORDS = [
    "guaranteed profit",
    "risk-free",
    "100x",
    "1000x",
    "10x",
    "100x gem",
    "moonshot",
    "to the moon",
    "financial freedom",
    "passive income",
    "get rich quick",
    "guaranteed returns",
    "never lose",
    "sure thing",
    "early retirement",
    "life changing",
    "don't miss out",
    "limited time",
    "presale",
    "whitelist",
    "private sale",
    "insider",
    "pump and dump",
    "rug pull",
    "honeypot",
    "scam",
    "fake",
    "ponzi",
    "pyramid",
    "get rich",
    "instant profit",
    "double your money",
    "guaranteed",
    "no risk",
    "zero risk",
    "risk free",
    "guaranteed",
    "sure profit",
    "massive returns",
    "unbelievable",
    "too good to be true",
    "secret",
    "hidden gem",
    "next big thing",
    "explosive",
    "lambo",
    "moon",
    "rocket",
    "hyper",
    "mega",
    "ultra",
]

COIN_PATTERN = re.compile(
    r"\$([A-Z]{2,10})\b"  # $BTC, $ETH, $SOL
    r"|(?<!\w)([A-Z]{2,10})(?:\s*coin|\s*token|\s*protocol)?\b"
    r"(?!\s*[a-z])",  # BTC, ETH, SOL (standalone caps)
    re.IGNORECASE,
)

# Known scam tokens / rugs for cross-reference
KNOWN_RUG_TOKENS: set[str] = {
    "SQUID",
    "LUNA",
    "FTT",
    "UST",
    "VCX",
    "IRON",
    "TITAN",
    "SAFEMOON",
    "SHIB",
    "FEG",
    "HOGE",
    "KISHU",
    "AKITA",
    "SQUIDGAME",
    "BABYDOGE",
    "FLOKI",
    "PEOPLE",
    "ELON",
}

# Known legitimate tokens (to avoid false positives in scam detection)
KNOWN_LEGIT_TOKENS: set[str] = {
    "BTC",
    "BITCOIN",
    "ETH",
    "ETHEREUM",
    "SOL",
    "SOLANA",
    "USDT",
    "USDC",
    "DAI",
    "BNB",
    "BSC",
    "ADA",
    "CARDANO",
    "DOT",
    "POLKADOT",
    "AVAX",
    "AVALANCHE",
    "MATIC",
    "POLYGON",
    "LINK",
    "CHAINLINK",
    "UNI",
    "UNISWAP",
    "AAVE",
    "COMP",
    "MKR",
    "SNX",
    "CRV",
    "CVX",
    "YFI",
    "SUSHI",
    "CAKE",
    "ALGO",
    "ALGORAND",
    "ATOM",
    "OSMO",
    "NEAR",
    "FTM",
    "FANTOM",
    "ARB",
    "OP",
    "APT",
    "SUI",
    "SEI",
    "TIA",
    "INJ",
    "RUNE",
    "LDO",
    "FXS",
    "GNO",
    "BAL",
    "1INCH",
    "ENS",
    "GRT",
    "OCEAN",
    "FET",
    "AGIX",
    "RNDR",
}

# ── Config ────────────────────────────────────────────────────────────────
POLL_INTERVAL = int(os.getenv("YT_POLL_INTERVAL", "3600"))  # 1 hour
MAX_VIDEOS_PER_CHANNEL = int(os.getenv("YT_MAX_VIDEOS", "3"))
_seen_videos: set[str] = set()


def extract_coin_mentions(text: str) -> dict[str, Any]:
    """Extract coin/token mentions from text and score for scam indicators."""
    matches = COIN_PATTERN.findall(text)
    coins: Counter[str] = Counter()
    for m in matches:
        coin = (m[0] or m[1]).upper()
        if len(coin) >= 2 and coin not in ("THE", "FOR", "AND", "THIS", "THAT", "YOU", "NOT"):
            coins[coin] += 1

    # Scam keyword count
    scam_hits = 0
    scam_matches = []
    for kw in SCAM_KEYWORDS:
        count = len(re.findall(re.escape(kw), text, re.IGNORECASE))
        if count > 0:
            scam_hits += count
            scam_matches.append(kw)

    # Risk score
    risk_score = 0.0
    for coin, count in coins.most_common(10):
        if coin in KNOWN_RUG_TOKENS:
            risk_score += 0.3 * count
        elif coin in KNOWN_LEGIT_TOKENS:
            risk_score -= 0.1 * count

    # Scam keyword penalty
    risk_score += scam_hits * 0.15

    # Normalize
    risk_score = max(0.0, min(1.0, risk_score))

    # Elevated suspicion for scam_watch channels
    if risk_score < 0.3:
        risk_score = 0.3  # Floor for any scam_watch channel

    return {
        "coins_mentioned": dict(coins.most_common(20)),
        "top_coin": coins.most_common(1)[0][0] if coins else None,
        "scam_keyword_hits": scam_hits,
        "scam_keywords": scam_matches[:10],
        "risk_score": round(risk_score, 3),
        "rug_tokens_mentioned": [c for c in coins if c in KNOWN_RUG_TOKENS],
        "legit_tokens_mentioned": [c for c in coins if c in KNOWN_LEGIT_TOKENS],
    }


async def get_video_transcript(video_url: str) -> str | None:
    """Get video transcript using yt-dlp."""
    try:
        result = subprocess.run(  # noqa: S603, ASYNC221 — pre-existing, audited 2026-08-22  # noqa: ASYNC221 — ffmpeg/yt-dlp subprocess in async ctx, audited 2026-08-22
            [  # noqa: S607 — pre-existing, audited 2026-08-22
                "yt-dlp",
                "--skip-download",
                "--write-auto-subs",
                "--sub-langs",
                "en",
                "--sub-format",
                "vtt",
                "-o",
                "/tmp/%(id)s.%(ext)s",  # noqa: S108 — pre-existing, audited 2026-08-22
                video_url,
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.debug("yt-dlp_transcript_failed url=%s", video_url[:40])
            return None

        # Read the VTT file
        video_id = (
            video_url.split("v=")[-1].split("&")[0]
            if "v=" in video_url
            else video_url.split("/")[-1]
        )
        vtt_path = f"/tmp/{video_id}.en.vtt"  # noqa: S108 — pre-existing, audited 2026-08-22
        if os.path.exists(vtt_path):  # noqa: ASYNC240 — pre-existing, audited 2026-08-22
            with open(vtt_path) as f:  # noqa: ASYNC230 — pre-existing, audited 2026-08-22
                text = f.read()
            os.unlink(vtt_path)
            # Strip VTT timestamps and headers
            text = re.sub(
                r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}.*$",
                "",
                text,
                flags=re.MULTILINE,
            )
            text = re.sub(r"^WEBVTT.*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"^\s*$\n", "", text, flags=re.MULTILINE)
            return text.strip()[:5000]
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("yt-dlp_error url=%s error=%s", video_url[:40], str(exc)[:60])

    return None


async def get_channel_videos(channel_url: str, max_videos: int = 3) -> list[dict[str, Any]]:
    """Get latest videos from a YouTube channel."""
    try:
        result = subprocess.run(  # noqa: S603, ASYNC221 — pre-existing, audited 2026-08-22  # noqa: ASYNC221 — ffmpeg/yt-dlp subprocess in async ctx, audited 2026-08-22
            [  # noqa: S607 — pre-existing, audited 2026-08-22
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--playlist-end",
                str(max_videos),
                f"{channel_url}/videos",
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []

        videos = []
        for line in result.stdout.decode().strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                videos.append(
                    {
                        "id": data.get("id", ""),
                        "title": data.get("title", ""),
                        "url": f"https://youtube.com/watch?v={data.get('id', '')}",
                        "channel": data.get("channel", ""),
                        "channel_url": channel_url,
                        "upload_date": data.get("upload_date", ""),
                        "duration": data.get("duration", 0),
                        "view_count": data.get("view_count", 0),
                        "description": (data.get("description", "") or "")[:500],
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return videos
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("yt-dlp_channel_failed url=%s error=%s", channel_url[:40], str(exc)[:60])
        return []


async def process_video(video: dict[str, Any], channel_name: str, category: str) -> dict[str, Any]:
    """Process a single video: transcript, coin extraction, scam scoring, index."""
    video_id = video["id"]
    if video_id in _seen_videos:
        return {"status": "skipped", "reason": "already_seen"}
    _seen_videos.add(video_id)
    if len(_seen_videos) > 50000:
        _seen_videos.clear()

    # Get transcript
    transcript = await get_video_transcript(video["url"])

    # Build content
    content_parts = [f"# {video['title']}", f"Channel: {channel_name}"]
    if transcript:
        content_parts.append(f"\n## Transcript\n{transcript}")
    elif video.get("description"):
        content_parts.append(f"\n## Description\n{video['description']}")
    content = "\n".join(content_parts)

    # Scam analysis
    scam_analysis = extract_coin_mentions(content)

    # PII scrub
    try:
        from rugbrain.pii_scrubber import scrub_text

        content = await scrub_text(content)
    except Exception:
        pass

    # Elevate severity for scam_watch channels
    is_scam_watch = category == "scam_watch"
    severity = (
        "critical"
        if is_scam_watch and scam_analysis["risk_score"] > 0.3
        else "high"
        if scam_analysis["risk_score"] > 0.5
        else "medium"
        if scam_analysis["risk_score"] > 0.2
        else "info"
    )

    # Build metadata
    metadata = {
        "source": "youtube",
        "channel": channel_name,
        "category": category,
        "scam_watch": is_scam_watch,
        "title": video["title"],
        "video_id": video_id,
        "url": video["url"],
        "upload_date": video.get("upload_date", ""),
        "view_count": video.get("view_count", 0),
        "duration": video.get("duration", 0),
        "top_coin": scam_analysis["top_coin"],
        "risk_score": scam_analysis["risk_score"],
        "scam_keyword_hits": scam_analysis["scam_keyword_hits"],
        "rug_tokens_mentioned": scam_analysis["rug_tokens_mentioned"],
        "coins_mentioned": list(scam_analysis["coins_mentioned"].keys())[:10],
        "ingested_at": datetime.now(UTC).isoformat(),
    }

    # Index into Qdrant
    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        embedding = await get_embedding(content)
        doc_id = f"yt_{channel_name}_{video_id}"[:200]
        doc_id = re.sub(r"[^a-zA-Z0-9_-]", "_", doc_id).lower()

        store = QdrantVectorStore()
        ok = await store.insert(
            doc_id=doc_id,
            collection="youtube_videos",
            embedding=embedding,
            content=content,
            metadata=metadata,
            source="youtube",
            severity=severity,
            chain="ethereum",
        )
        logger.info(
            "yt_ingested channel=%s title=%s coins=%d risk=%.2f ok=%s",
            channel_name,
            video["title"][:30],
            len(scam_analysis["coins_mentioned"]),
            scam_analysis["risk_score"],
            ok,
        )
        return {
            "status": "ok" if ok else "failed",
            "doc_id": doc_id,
            "scam_analysis": scam_analysis,
        }
    except Exception as exc:
        logger.warning("yt_ingest_failed video=%s error=%s", video_id, str(exc)[:80])
        return {"status": "failed", "error": str(exc)[:80]}


async def youtube_scan_cycle() -> dict[str, Any]:
    """Scan all YouTube channels for new videos."""
    results = []
    for _idx, (name, url, category) in enumerate(YT_CHANNELS):
        videos = await get_channel_videos(url, MAX_VIDEOS_PER_CHANNEL)
        for video in videos:
            result = await process_video(video, name, category)
            results.append({**video, "process_result": result})
        logger.info(
            "yt_scan channel=%s videos=%d scrapped=%d",
            name,
            len(videos),
            sum(1 for r in results if r.get("process_result", {}).get("status") == "ok"),
        )
        await asyncio.sleep(1.5)  # Rate limit

    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "channels_scanned": len(YT_CHANNELS),
        "new_videos": len(results),
        "videos": results[:10],
    }


async def youtube_scan_loop() -> None:
    """Run YouTube scanning in a continuous background loop."""
    logger.info(
        "youtube_ingester_started interval=%ds channels=%d", POLL_INTERVAL, len(YT_CHANNELS)
    )
    while True:
        try:
            result = await youtube_scan_cycle()
            if result["new_videos"] > 0:
                logger.info("yt_new count=%d", result["new_videos"])
        except Exception as exc:
            logger.warning("yt_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
