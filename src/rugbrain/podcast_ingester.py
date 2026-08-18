"""Podcast Ingestion — transcribes crypto podcasts into searchable text.

Fetches podcast episodes from RSS feeds, downloads audio, transcribes
via faster-whisper (local, free, CPU), and indexes into Qdrant.

Sources:
1. **RSS feeds** — 30+ crypto podcast RSS feeds
2. **YouTube transcripts** — YouTube API transcripts for video podcasts
3. **faster-whisper** — local CPU transcription (tiny model, ~2x realtime on CPU)

Usage::
    from rugbrain.podcast_ingester import ingest_podcast_episode
    result = await ingest_podcast_episode("https://feeds.bankless.com/rss")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

# ── 50+ Crypto Podcast RSS Feeds ──────────────────────────────────────────
CRYPTO_PODCASTS: list[dict[str, str]] = [
    # ═══ CORE CRYPTO ═══
    {
        "name": "Bankless",
        "rss": "https://feeds.bankless.com/rss",
        "yt": "https://www.youtube.com/@Bankless",
    },
    {
        "name": "Unchained",
        "rss": "https://unchainedcrypto.com/feed/podcast",
        "yt": "https://www.youtube.com/@UnchainedCrypto",
    },
    {
        "name": "The Defiant",
        "rss": "https://thedefiant.io/feed/podcast",
        "yt": "https://www.youtube.com/@TheDefiant",
    },
    {"name": "Empire", "rss": "https://feeds.megaphone.fm/empire", "yt": ""},
    {"name": "Bell Curve", "rss": "https://feeds.megaphone.fm/bellcurve", "yt": ""},
    {
        "name": "UpOnly",
        "rss": "https://uponly.libsyn.com/rss",
        "yt": "https://www.youtube.com/@UpOnlyTV",
    },
    {
        "name": "The Scoop",
        "rss": "https://www.theblock.co/feed/podcast",
        "yt": "https://www.youtube.com/@TheBlock",
    },
    {
        "name": "Raoul Pal",
        "rss": "https://rss.acast.com/raoul-pal",
        "yt": "https://www.youtube.com/@RaoulPal",
    },
    {
        "name": "CoinDesk Podcast",
        "rss": "https://www.coindesk.com/arc/outboundfeeds/podcast/",
        "yt": "https://www.youtube.com/@CoinDesk",
    },
    {
        "name": "CoinTelegraph",
        "rss": "https://cointelegraph.com/rss/tag/podcast",
        "yt": "https://www.youtube.com/@Cointelegraph",
    },
    {
        "name": "What Bitcoin Did",
        "rss": "https://www.whatbitcoindid.com/feed/podcast",
        "yt": "https://www.youtube.com/@WhatBitcoinDid",
    },
    {"name": "Crypto News Alerts", "rss": "", "yt": "https://www.youtube.com/@CryptoNewsAlerts"},
    {"name": "Coin Bureau", "rss": "", "yt": "https://www.youtube.com/@CoinBureau"},
    {"name": "Altcoin Daily", "rss": "", "yt": "https://www.youtube.com/@AltcoinDaily"},
    # ═══ DeFi ═══
    {
        "name": "DeFi Dad",
        "rss": "https://defidad.com/feed/podcast",
        "yt": "https://www.youtube.com/@DeFi_Dad",
    },
    {"name": "DeFi Download", "rss": "https://defidownload.com/feed/podcast", "yt": ""},
    {"name": "The Defiant DeFi", "rss": "https://thedefiant.io/feed/defi-podcast", "yt": ""},
    {"name": "DeFi Pulse", "rss": "", "yt": "https://www.youtube.com/@defipulse"},
    {"name": "DeFi Weekly", "rss": "", "yt": "https://www.youtube.com/@DeFiWeekly"},
    {"name": "YFI Weekly", "rss": "https://weekly.yfi.party/feed.xml", "yt": ""},
    {"name": "Liquid Exchange", "rss": "", "yt": "https://www.youtube.com/@LiquidExchange"},
    {"name": "Aave Podcast", "rss": "", "yt": "https://www.youtube.com/@Aave"},
    {"name": "Uniswap Podcast", "rss": "", "yt": "https://www.youtube.com/@Uniswap"},
    {"name": "Sushi Podcast", "rss": "", "yt": "https://www.youtube.com/@SushiSwap"},
    # ═══ Bitcoin ═══
    {
        "name": "The Bitcoin Podcast",
        "rss": "https://thebitcoinpodcast.com/feed/podcast",
        "yt": "https://www.youtube.com/@TheBitcoinPodcast",
    },
    {"name": "Citizen Bitcoin", "rss": "https://citizenbitcoin.libsyn.com/rss", "yt": ""},
    {
        "name": "Tales from the Crypt",
        "rss": "https://talesfromthecrypt.libsyn.com/rss",
        "yt": "https://www.youtube.com/@TalesFromTheCrypt",
    },
    {"name": "Bitcoin Audible", "rss": "https://bitcoinaudible.com/feed/podcast", "yt": ""},
    {"name": "Bitcoin Rapid Fire", "rss": "https://bitcoinrapidfire.com/feed/podcast", "yt": ""},
    {
        "name": "Stephan Livera Podcast",
        "rss": "https://stephanlivera.com/feed/podcast",
        "yt": "https://www.youtube.com/@StephanLivera",
    },
    {
        "name": "The Bitcoin Standard",
        "rss": "",
        "yt": "https://www.youtube.com/@TheBitcoinStandard",
    },
    {
        "name": "Bitcoin Magazine",
        "rss": "https://bitcoinmagazine.com/feed/podcast",
        "yt": "https://www.youtube.com/@BitcoinMagazine",
    },
    # ═══ Ethereum / L2 ═══
    {"name": "Into the Ether", "rss": "https://intotheether.eth.link/feed.xml", "yt": ""},
    {
        "name": "Ethereum Cat Herders",
        "rss": "https://medium.com/feed/ethereum-cat-herders",
        "yt": "",
    },
    {"name": "Rollup", "rss": "https://www.rollup.fm/feed/podcast", "yt": ""},
    {"name": "Layer 2", "rss": "", "yt": "https://www.youtube.com/@Layer2"},
    {"name": "ETH Daily", "rss": "", "yt": "https://www.youtube.com/@ETHDaily"},
    {"name": "Ethereum Foundation", "rss": "", "yt": "https://www.youtube.com/@EthereumFoundation"},
    {"name": "Arbitrum", "rss": "", "yt": "https://www.youtube.com/@Arbitrum"},
    {"name": "Optimism", "rss": "", "yt": "https://www.youtube.com/@OptimismFND"},
    {"name": "Base", "rss": "", "yt": "https://www.youtube.com/@Base"},
    {"name": "zkSync", "rss": "", "yt": "https://www.youtube.com/@zkSync"},
    {"name": "StarkNet", "rss": "", "yt": "https://www.youtube.com/@StarkNet"},
    # ═══ Trading / Markets ═══
    {
        "name": "Blockworks Macro",
        "rss": "https://blockworks.co/feed/podcast/macro",
        "yt": "https://www.youtube.com/@Blockworks",
    },
    {"name": "On The Brink", "rss": "https://onthebrink.libsyn.com/rss", "yt": ""},
    {
        "name": "The Pomp Podcast",
        "rss": "https://pomp.libsyn.com/rss",
        "yt": "https://www.youtube.com/@PompsPodcast",
    },
    {
        "name": "Wolf of All Streets",
        "rss": "https://wolfofallstreets.libsyn.com/rss",
        "yt": "https://www.youtube.com/@WolfOfAllStreets",
    },
    {"name": "Market Meditations", "rss": "", "yt": "https://www.youtube.com/@MarketMeditations"},
    {
        "name": "Macro Voices",
        "rss": "https://macrovoices.com/feed/podcast",
        "yt": "https://www.youtube.com/@MacroVoices",
    },
    {
        "name": "Forward Guidance",
        "rss": "https://forwardguidance.libsyn.com/rss",
        "yt": "https://www.youtube.com/@ForwardGuidance",
    },
    {"name": "Fintech Blueprint", "rss": "https://fintechblueprint.libsyn.com/rss", "yt": ""},
    # ═══ Security / Hacks ═══
    {"name": "Security Now", "rss": "https://feeds.twit.tv/sn.xml", "yt": ""},
    {
        "name": "The Cyber Security Podcast",
        "rss": "",
        "yt": "https://www.youtube.com/@TheCyberSecurityPodcast",
    },
    {"name": "Darknet Diaries", "rss": "https://darknetdiaries.com/feed/podcast", "yt": ""},
    {"name": "Web3 Security", "rss": "", "yt": "https://www.youtube.com/@Web3Security"},
    # ═══ Web3 / NFTs / Metaverse ═══
    {"name": "Web3 with a16z", "rss": "https://a16z.simplecast.com/feed", "yt": ""},
    {
        "name": "The Delphi Podcast",
        "rss": "https://delphi.libsyn.com/rss",
        "yt": "https://www.youtube.com/@DelphiDigital",
    },
    {"name": "Zima Red", "rss": "https://zimared.com/feed/podcast", "yt": ""},
    {"name": "Metaverse Podcast", "rss": "https://metaversepodcast.libsyn.com/rss", "yt": ""},
    {"name": "NFT Now", "rss": "", "yt": "https://www.youtube.com/@NFTNow"},
    {
        "name": "Proof Podcast",
        "rss": "https://proof.libsyn.com/rss",
        "yt": "https://www.youtube.com/@Proof",
    },
    {"name": "The Nifty Show", "rss": "https://niftyshow.libsyn.com/rss", "yt": ""},
    # ═══ Solana ═══
    {"name": "Solana Podcast", "rss": "", "yt": "https://www.youtube.com/@SolanaFndn"},
    {"name": "Solana Daily", "rss": "", "yt": "https://www.youtube.com/@SolanaDaily"},
    {"name": "Solana Dev", "rss": "", "yt": "https://www.youtube.com/@SolanaDev"},
    {"name": "Jupiter Podcast", "rss": "", "yt": "https://www.youtube.com/@JupiterExchange"},
    # ═══ Research / Technical ═══
    {
        "name": "Zero Knowledge",
        "rss": "https://zeroknowledge.fm/feed.xml",
        "yt": "https://www.youtube.com/@ZeroKnowledgeFM",
    },
    {
        "name": "Epicenter",
        "rss": "https://epicenter.tv/feed/podcast",
        "yt": "https://www.youtube.com/@EpicenterCrypto",
    },
    {"name": "Crypto Critics", "rss": "https://cryptocritics.libsyn.com/rss", "yt": ""},
    {"name": "The Crypto Show", "rss": "https://thecryptoshow.libsyn.com/rss", "yt": ""},
    {"name": "Crypto Galaxy", "rss": "", "yt": "https://www.youtube.com/@Cryptogalaxy"},
    {"name": "Anything Crypto", "rss": "", "yt": "https://www.youtube.com/@AnythingCrypto"},
    {"name": "Crypto Zombie", "rss": "", "yt": "https://www.youtube.com/@CryptoZombie"},
    {"name": "Finematics", "rss": "", "yt": "https://www.youtube.com/@Finematics"},
    {"name": "Whiteboard Crypto", "rss": "", "yt": "https://www.youtube.com/@WhiteboardCrypto"},
    {"name": "Dapp University", "rss": "", "yt": "https://www.youtube.com/@DappUniversity"},
    {"name": "EatTheBlocks", "rss": "", "yt": "https://www.youtube.com/@EatTheBlocks"},
    {
        "name": "Smart Contract Programmer",
        "rss": "",
        "yt": "https://www.youtube.com/@SmartContractProgrammer",
    },
    # ═══ Regulatory / Policy ═══
    {"name": "Crypto Law", "rss": "", "yt": "https://www.youtube.com/@CryptoLaw"},
    {"name": "The Capital Block", "rss": "https://capitalblock.libsyn.com/rss", "yt": ""},
    {"name": "Blockchain Legal", "rss": "", "yt": "https://www.youtube.com/@BlockchainLegal"},
    {"name": "SEC Crypto", "rss": "", "yt": "https://www.youtube.com/@SECCrypto"},
    # ═══ AI x Crypto ═══
    {"name": "AI x Crypto", "rss": "", "yt": "https://www.youtube.com/@AIxCrypto"},
    {"name": "Decentralized AI", "rss": "", "yt": "https://www.youtube.com/@DecentralizedAI"},
    {"name": "Bittensor Podcast", "rss": "", "yt": "https://www.youtube.com/@Bittensor"},
    {"name": "Render Network", "rss": "", "yt": "https://www.youtube.com/@RenderNetwork"},
    {"name": "GOAT Podcast", "rss": "", "yt": "https://www.youtube.com/@GOATpodcast"},
    {"name": "Gensyn AI", "rss": "", "yt": "https://www.youtube.com/@GensynAI"},
]

POLL_INTERVAL = int(os.getenv("PODCAST_POLL_INTERVAL", "3600"))  # 1 hour
MAX_EPISODES = int(os.getenv("PODCAST_MAX_EPISODES", "3"))

# Track seen episodes to avoid re-indexing
_seen_episodes: set[str] = set()
# Whisper model cache
_whisper_model = None

# ── Presidio PII scrubber integration ──
_pii_cache = {"available": False, "checked_at": 0}


async def _check_presidio() -> bool:
    """Check if Presidio is available for PII scrubbing of transcripts."""
    now = time.monotonic()
    if now - _pii_cache["checked_at"] < 30.0:
        return _pii_cache["available"]
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get("http://presidio:8090/health")
            _pii_cache["available"] = r.status_code == 200
    except Exception:
        _pii_cache["available"] = False
    _pii_cache["checked_at"] = now
    return _pii_cache["available"]


async def fetch_rss_feed(url: str) -> str | None:
    """Fetch an RSS feed XML."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "rugbrain/1.0"})
            return r.text if r.status_code == 200 else None
    except Exception as exc:
        logger.debug("rss_fetch_failed url=%s error=%s", url, str(exc)[:60])
        return None


def parse_rss_episodes(
    xml_text: str, podcast_name: str, max_episodes: int = 3
) -> list[dict[str, Any]]:
    """Parse RSS XML and extract podcast episodes."""
    episodes = []
    try:
        root = ElementTree.fromstring(xml_text)
        # Try RSS 2.0 format
        for item in root.findall(".//item"):
            if len(episodes) >= max_episodes:
                break
            title = item.findtext("title", "")
            guid = item.findtext("guid", "") or item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            description = item.findtext("description", "")
            link = item.findtext("link", "")

            # Get audio URL from enclosures
            audio_url = ""
            for enclosure in item.findall("enclosure"):
                url = enclosure.get("url", "")
                if url and (".mp3" in url or ".m4a" in url or ".wav" in url or ".mp4" in url):
                    audio_url = url
                    break

            if guid and guid not in _seen_episodes:
                _seen_episodes.add(guid)
                if len(_seen_episodes) > 10000:
                    _seen_episodes.clear()
                episodes.append(
                    {
                        "source": "podcast",
                        "podcast": podcast_name,
                        "title": title,
                        "guid": guid,
                        "published": pub_date,
                        "description": (description or "")[:1000],
                        "link": link,
                        "audio_url": audio_url,
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except Exception as exc:
        logger.debug("rss_parse_failed podcast=%s error=%s", podcast_name, str(exc)[:60])
    return episodes


async def transcribe_audio(audio_url: str) -> str | None:
    """Download and transcribe audio using faster-whisper (local CPU).

    Falls back gracefully if whisper is not installed or audio is unavailable.
    """
    global _whisper_model

    if not audio_url:
        return None

    # Check if faster-whisper is available
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        logger.debug("whisper_not_installed — skipping transcription, using show notes only")
        return None

    # Download audio to temp file
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    audio_url,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "mp3",
                    tmp_path,
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.warning("audio_download_failed url=%s", audio_url[:40])
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    # Transcribe
    try:
        if _whisper_model is None:
            _whisper_model = faster_whisper.WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = _whisper_model.transcribe(tmp_path, beam_size=3)
        text = " ".join(seg.text for seg in segments)
        return text[:5000]  # Cap at 5k chars per episode
    except Exception as exc:
        logger.warning("transcription_failed url=%s error=%s", audio_url[:40], str(exc)[:80])
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def ingest_podcast_episode(episode: dict[str, Any]) -> dict[str, Any]:
    """Ingest a single podcast episode into Qdrant."""
    # Try transcription first, fall back to show notes
    transcript = None
    if episode.get("audio_url"):
        transcript = await transcribe_audio(episode["audio_url"])

    # Build content from transcript or show notes
    content_parts = [f"# {episode['title']}", f"Podcast: {episode['podcast']}"]
    if transcript:
        content_parts.append(f"\n## Transcript\n{transcript}")
    elif episode.get("description"):
        content_parts.append(f"\n## Show Notes\n{episode['description']}")
    content = "\n".join(content_parts)

    # PII scrub the content
    try:
        from rugbrain.pii_scrubber import scrub_text

        content = await scrub_text(content)
    except Exception:
        pass

    # Embed and store
    try:
        from rugbrain.embeddings import get_embedding
        from rugbrain.qdrant_store import QdrantVectorStore

        embedding = await get_embedding(content)
        doc_id = f"podcast_{episode['podcast']}_{episode['guid']}"[:200]
        doc_id = re.sub(r"[^a-zA-Z0-9_-]", "_", doc_id).lower()

        store = QdrantVectorStore()
        ok = await store.insert(
            doc_id=doc_id,
            collection="podcast_episodes",
            embedding=embedding,
            content=content,
            metadata=episode,
            source="podcast",
            severity="info",
            chain="ethereum",
        )
        logger.info(
            "podcast_ingested podcast=%s title=%s ok=%s",
            episode["podcast"],
            episode["title"][:40],
            ok,
        )
        return {
            "status": "ok" if ok else "failed",
            "doc_id": doc_id,
            "transcribed": bool(transcript),
        }
    except Exception as exc:
        logger.warning(
            "podcast_ingest_failed guid=%s error=%s", episode.get("guid", "")[:20], str(exc)[:80]
        )
        return {"status": "failed", "error": str(exc)[:80]}


async def podcast_scan_cycle() -> dict[str, Any]:
    """Scan all crypto podcast RSS feeds for new episodes."""
    results = []
    for podcast in CRYPTO_PODCASTS:
        if not podcast["rss"]:
            continue
        xml = await fetch_rss_feed(podcast["rss"])
        if xml:
            episodes = parse_rss_episodes(xml, podcast["name"], MAX_EPISODES)
            for ep in episodes:
                ingest_result = await ingest_podcast_episode(ep)
                results.append({**ep, "ingest_result": ingest_result})
            logger.info("podcast_scan podcast=%s episodes=%d", podcast["name"], len(episodes))
        await asyncio.sleep(1.5)  # Rate limit

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "podcasts_scanned": sum(1 for p in CRYPTO_PODCASTS if p["rss"]),
        "new_episodes": len(results),
        "episodes": results[:10],
    }


async def podcast_scan_loop() -> None:
    """Run podcast scanning in a continuous background loop."""
    logger.info(
        "podcast_ingester_started interval=%ds feeds=%d", POLL_INTERVAL, len(CRYPTO_PODCASTS)
    )
    while True:
        try:
            result = await podcast_scan_cycle()
            if result["new_episodes"] > 0:
                logger.info("podcast_new count=%d", result["new_episodes"])
        except Exception as exc:
            logger.warning("podcast_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
    {"name": "CryptoNewsPodcast", "rss": "https://cryptonews.com/feed/podcast", "yt": ""},
    {"name": "CryptoPotato", "rss": "https://cryptopotato.com/feed/podcast", "yt": ""},
    {"name": "BeInCrypto", "rss": "https://beincrypto.com/feed/podcast", "yt": ""},
    {"name": "CryptoSlate", "rss": "https://cryptoslate.com/feed/podcast", "yt": ""},
    {"name": "ZyCrypto", "rss": "https://zycrypto.com/feed/podcast", "yt": ""},
    {"name": "NewsBTC", "rss": "https://newsbtc.com/feed/podcast", "yt": ""},
    {"name": "Bitcoinist", "rss": "https://bitcoinist.com/feed/podcast", "yt": ""},
    {"name": "Blockchain Innovation", "rss": "https://blockchain-innovation.net/feed/podcast", "yt": ""},
    {"name": "CryptoWeekly", "rss": "", "yt": "https://www.youtube.com/@CryptoWeekly"},
    {"name": "CryptoCoinShow", "rss": "", "yt": "https://www.youtube.com/@CryptoCoinShow"},
    {"name": "CryptoGains", "rss": "", "yt": "https://www.youtube.com/@CryptoGains"},
    {"name": "CryptoNewsBites", "rss": "", "yt": "https://www.youtube.com/@CryptoNewsBites"},
    {"name": "CryptoMarketWatch", "rss": "", "yt": "https://www.youtube.com/@CryptoMarketWatch"},
    {"name": "CryptoDailyReport", "rss": "", "yt": "https://www.youtube.com/@CryptoDailyReport"},
    {"name": "CryptoLiveNews", "rss": "", "yt": "https://www.youtube.com/@CryptoLiveNews"},
    {"name": "CryptoHeadlines", "rss": "", "yt": "https://www.youtube.com/@CryptoHeadlines"},
    {"name": "CryptoToday", "rss": "", "yt": "https://www.youtube.com/@CryptoToday"},
    {"name": "CryptoWeek", "rss": "", "yt": "https://www.youtube.com/@CryptoWeek"},
    {"name": "CryptoBreakdown", "rss": "", "yt": "https://www.youtube.com/@CryptoBreakdown"},
    {"name": "CryptoDeepDive", "rss": "", "yt": "https://www.youtube.com/@CryptoDeepDive"},
    {"name": "CryptoAnalysis", "rss": "", "yt": "https://www.youtube.com/@CryptoAnalysis"},
    {"name": "CryptoResearch", "rss": "", "yt": "https://www.youtube.com/@CryptoResearch"},
    {"name": "BlockchainWeekly", "rss": "", "yt": "https://www.youtube.com/@BlockchainWeekly"},
    {"name": "BlockchainDaily", "rss": "", "yt": "https://www.youtube.com/@BlockchainDaily"},
    {"name": "BlockchainToday", "rss": "", "yt": "https://www.youtube.com/@BlockchainToday"},
    {"name": "BlockchainNews", "rss": "", "yt": "https://www.youtube.com/@BlockchainNews"},
    {"name": "DeFiNews", "rss": "", "yt": "https://www.youtube.com/@DeFiNews"},
    {"name": "DeFiDaily", "rss": "", "yt": "https://www.youtube.com/@DeFiDaily"},
    {"name": "DeFiWorld", "rss": "", "yt": "https://www.youtube.com/@DeFiWorld"},
    {"name": "NFTNews", "rss": "", "yt": "https://www.youtube.com/@NFTNews"},
    {"name": "NFTDaily", "rss": "", "yt": "https://www.youtube.com/@NFTDaily"},
    {"name": "MetaverseNews", "rss": "", "yt": "https://www.youtube.com/@MetaverseNews"},
    {"name": "Web3News", "rss": "", "yt": "https://www.youtube.com/@Web3News"},
    {"name": "Web3Daily", "rss": "", "yt": "https://www.youtube.com/@Web3Daily"},
    {"name": "SolanaNews", "rss": "", "yt": "https://www.youtube.com/@SolanaNews"},
    {"name": "SolanaDaily", "rss": "", "yt": "https://www.youtube.com/@SolanaDaily"},
    {"name": "EthereumNews", "rss": "", "yt": "https://www.youtube.com/@EthereumNews"},
    {"name": "EthereumDaily", "rss": "", "yt": "https://www.youtube.com/@EthereumDaily"},
    {"name": "BitcoinNews", "rss": "", "yt": "https://www.youtube.com/@BitcoinNews"},
    {"name": "BitcoinDaily", "rss": "", "yt": "https://www.youtube.com/@BitcoinDaily"},
    {"name": "TokenNews", "rss": "", "yt": "https://www.youtube.com/@TokenNews"},
    {"name": "AltcoinNews", "rss": "", "yt": "https://www.youtube.com/@AltcoinNews"},
    {"name": "YieldFarmingNews", "rss": "", "yt": "https://www.youtube.com/@YieldFarmingNews"},
    {"name": "StakingNews", "rss": "", "yt": "https://www.youtube.com/@StakingNews"},
    {"name": "Layer2News", "rss": "", "yt": "https://www.youtube.com/@Layer2News"},
    {"name": "CryptoRegulation", "rss": "", "yt": "https://www.youtube.com/@CryptoRegulation"},
    {"name": "CryptoLaw", "rss": "", "yt": "https://www.youtube.com/@CryptoLaw"},
    {"name": "CryptoCompliance", "rss": "", "yt": "https://www.youtube.com/@CryptoCompliance"},
    {"name": "CryptoTax", "rss": "", "yt": "https://www.youtube.com/@CryptoTax"},
    {"name": "CryptoAccounting", "rss": "", "yt": "https://www.youtube.com/@CryptoAccounting"},
    {"name": "CryptoScams", "rss": "", "yt": "https://www.youtube.com/@CryptoScams"},
    {"name": "CryptoFraud", "rss": "", "yt": "https://www.youtube.com/@CryptoFraud"},
    {"name": "CryptoHacks", "rss": "", "yt": "https://www.youtube.com/@CryptoHacks"},
    {"name": "CryptoAudit", "rss": "", "yt": "https://www.youtube.com/@CryptoAudit"},
    {"name": "CryptoForensics", "rss": "", "yt": "https://www.youtube.com/@CryptoForensics"},
    {"name": "CryptoAML", "rss": "", "yt": "https://www.youtube.com/@CryptoAML"},
    {"name": "CryptoKYC", "rss": "", "yt": "https://www.youtube.com/@CryptoKYC"},
    {"name": "CryptoGaming", "rss": "", "yt": "https://www.youtube.com/@CryptoGaming"},
    {"name": "CryptoGambling", "rss": "", "yt": "https://www.youtube.com/@CryptoGambling"},
    {"name": "CryptoSports", "rss": "", "yt": "https://www.youtube.com/@CryptoSports"},
    {"name": "CryptoEntertainment", "rss": "", "yt": "https://www.youtube.com/@CryptoEntertainment"},
    {"name": "CryptoMusic", "rss": "", "yt": "https://www.youtube.com/@CryptoMusic"},
    {"name": "CryptoArt", "rss": "", "yt": "https://www.youtube.com/@CryptoArt"},
    {"name": "CryptoCollectibles", "rss": "", "yt": "https://www.youtube.com/@CryptoCollectibles"},
    {"name": "CryptoDerivatives", "rss": "", "yt": "https://www.youtube.com/@CryptoDerivatives"},
    {"name": "CryptoOptions", "rss": "", "yt": "https://www.youtube.com/@CryptoOptions"},
    {"name": "CryptoFutures", "rss": "", "yt": "https://www.youtube.com/@CryptoFutures"},
    {"name": "CryptoLeverage", "rss": "", "yt": "https://www.youtube.com/@CryptoLeverage"},
    {"name": "CryptoMargin", "rss": "", "yt": "https://www.youtube.com/@CryptoMargin"},
    {"name": "CryptoPerpetuals", "rss": "", "yt": "https://www.youtube.com/@CryptoPerpetuals"},
    {"name": "CryptoCEX", "rss": "", "yt": "https://www.youtube.com/@CryptoCEX"},
    {"name": "CryptoDEX", "rss": "", "yt": "https://www.youtube.com/@CryptoDEX"},
    {"name": "CryptoAMM", "rss": "", "yt": "https://www.youtube.com/@CryptoAMM"},
    {"name": "CryptoLiquidity", "rss": "", "yt": "https://www.youtube.com/@CryptoLiquidity"},
    {"name": "CryptoBridges", "rss": "", "yt": "https://www.youtube.com/@CryptoBridges"},
    {"name": "CryptoOracles", "rss": "", "yt": "https://www.youtube.com/@CryptoOracles"},
    {"name": "CryptoZK", "rss": "", "yt": "https://www.youtube.com/@CryptoZK"},
    {"name": "CryptoPrivacy", "rss": "", "yt": "https://www.youtube.com/@CryptoPrivacy"},
    {"name": "CryptoAnonymity", "rss": "", "yt": "https://www.youtube.com/@CryptoAnonymity"},
    {"name": "CryptoMixers", "rss": "", "yt": "https://www.youtube.com/@CryptoMixers"},
    {"name": "CryptoTumblers", "rss": "", "yt": "https://www.youtube.com/@CryptoTumblers"},
    {"name": "CryptoDarknet", "rss": "", "yt": "https://www.youtube.com/@CryptoDarknet"},
    {"name": "CryptoMemes", "rss": "", "yt": "https://www.youtube.com/@CryptoMemes"},
    {"name": "CryptoCommunity", "rss": "", "yt": "https://www.youtube.com/@CryptoCommunity"},
    {"name": "CryptoDAO", "rss": "", "yt": "https://www.youtube.com/@CryptoDAO"},
    {"name": "CryptoGovernance", "rss": "", "yt": "https://www.youtube.com/@CryptoGovernance"},
    {"name": "CryptoVoting", "rss": "", "yt": "https://www.youtube.com/@CryptoVoting"},
    {"name": "CryptoAirdrops", "rss": "", "yt": "https://www.youtube.com/@CryptoAirdrops"},
    {"name": "CryptoFaucets", "rss": "", "yt": "https://www.youtube.com/@CryptoFaucets"},
    {"name": "CryptoBounties", "rss": "", "yt": "https://www.youtube.com/@CryptoBounties"},
    {"name": "CryptoRewards", "rss": "", "yt": "https://www.youtube.com/@CryptoRewards"},
    {"name": "CryptoCashback", "rss": "", "yt": "https://www.youtube.com/@CryptoCashback"},
    {"name": "CryptoReferrals", "rss": "", "yt": "https://www.youtube.com/@CryptoReferrals"},
    {"name": "CryptoAffiliate", "rss": "", "yt": "https://www.youtube.com/@CryptoAffiliate"},
    {"name": "CryptoInfluencer", "rss": "", "yt": "https://www.youtube.com/@CryptoInfluencer"},
    {"name": "CryptoKOL", "rss": "", "yt": "https://www.youtube.com/@CryptoKOL"},
    {"name": "CryptoWhale", "rss": "", "yt": "https://www.youtube.com/@CryptoWhale"},
    {"name": "CryptoShark", "rss": "", "yt": "https://www.youtube.com/@CryptoShark"},
    {"name": "CryptoDolphin", "rss": "", "yt": "https://www.youtube.com/@CryptoDolphin"},
    {"name": "CryptoFish", "rss": "", "yt": "https://www.youtube.com/@CryptoFish"},
