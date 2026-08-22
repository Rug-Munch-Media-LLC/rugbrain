"""
Multi-Modal RAG - Vision + Text retrieval for crypto security.

Capabilities:
  1. Token logo analysis - stolen artwork detection via perceptual hashing
  2. Screenshot OCR - extract addresses, contracts, amounts from images
  3. Chart pattern analysis - detect pump/dump, rug pull patterns in price charts

Uses:
  - Perceptual hashing (pHash) for image similarity - zero API cost
  - DeepSeek v4 Flash vision for description/captioning (via LLM_API_KEY)
  - Text embedding via Qwen3-4B for cross-modal retrieval

Designed to work with existing RAG pipeline: images → descriptions → embed → search.
"""

import hashlib
import logging
import os
from typing import Any, cast

logger = logging.getLogger(__name__)

# ── Perceptual Hashing ───────────────────────────────────────────


def compute_image_hash(image_bytes: bytes, hash_size: int = 16) -> str:
    """
    Compute perceptual hash (pHash) of an image.

    Uses average hashing - resize to hash_sizexhash_size, convert to grayscale,  # noqa: RUF002
    compute average, threshold each pixel. Returns hex string.

    Pure Python implementation - no OpenCV/pillow dependency.
    """
    # Parse minimal BMP/PNG header to get pixel data
    try:
        # Use Python's built-in imghdr to detect type
        import imghdr

        img_type = imghdr.what(None, image_bytes)

        if img_type == "png":
            import struct
            import zlib

            # Minimal PNG parser for pHash
            # Skip to IDAT chunks
            pos = 8  # Skip PNG signature
            width = height = 0
            pixel_data = b""

            while pos < len(image_bytes):
                chunk_len = struct.unpack(">I", image_bytes[pos : pos + 4])[0]
                chunk_type = image_bytes[pos + 4 : pos + 8].decode("ascii", errors="ignore")
                pos += 8

                if chunk_type == "IHDR":
                    width = struct.unpack(">I", image_bytes[pos : pos + 4])[0]
                    height = struct.unpack(">I", image_bytes[pos + 4 : pos + 8])[0]
                elif chunk_type == "IDAT":
                    pixel_data += image_bytes[pos : pos + chunk_len]
                elif chunk_type == "IEND":
                    break

                pos += chunk_len + 4  # +4 for CRC

            if pixel_data:
                try:
                    decompressed = zlib.decompress(pixel_data)
                    # Convert to 16x16 grayscale by averaging blocks
                    return _hash_from_raw(decompressed, width, height, hash_size)
                except zlib.error:
                    pass

        # Fallback: hash raw bytes (file-level similarity)
        return _hash_from_raw(image_bytes, 1, len(image_bytes), hash_size)

    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    # Ultimate fallback: SHA-based hash of raw bytes
    return hashlib.sha256(image_bytes).hexdigest()[: hash_size * 2]


def _hash_from_raw(data: bytes, width: int, height: int, hash_size: int) -> str:
    """Compute average hash from raw pixel data."""
    try:
        list(data)
        block_w = max(1, width // hash_size)
        block_h = max(1, height // hash_size)

        hash_bits = []
        for y in range(hash_size):
            for x in range(hash_size):
                # Average pixels in this block
                block_sum = 0
                block_count = 0
                for by in range(y * block_h, min((y + 1) * block_h, height)):
                    for bx in range(x * block_w, min((x + 1) * block_w, width)):
                        idx = (by * width + bx) * 3  # Assume RGB
                        if idx + 2 < len(data):
                            # Grayscale: 0.299*R + 0.587*G + 0.114*B
                            gray = int(
                                0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2]
                            )
                            block_sum += gray
                            block_count += 1
                block_avg = block_sum / max(block_count, 1)
                hash_bits.append(block_avg)

        # Compute average and threshold
        total_avg = sum(hash_bits) / len(hash_bits)
        bits = "".join("1" if b > total_avg else "0" for b in hash_bits)

        # Convert to hex
        return hex(int(bits, 2))[2:].zfill(hash_size * 2 // 4)

    except Exception:
        return hashlib.sha256(data).hexdigest()[: hash_size * 2]


def compare_image_hashes(hash1: str, hash2: str) -> float:
    """Compare two perceptual hashes. Returns similarity 0-1."""
    if len(hash1) != len(hash2):
        return 0.0
    try:
        h1 = int(hash1, 16)
        h2 = int(hash2, 16)
        xor = h1 ^ h2
        # Count differing bits
        diff_bits = bin(xor).count("1")
        max_bits = len(hash1) * 4
        return 1.0 - (diff_bits / max_bits)
    except ValueError:
        return 0.0


# ── Token Logo Database ───────────────────────────────────────────


class TokenLogoDB:
    """In-memory database of known token logos for stolen artwork detection."""

    def __init__(self) -> None:
        self._logos: dict[str, dict[str, Any]] = {}

    def add_logo(
        self,
        token_symbol: str,
        token_address: str,
        chain: str,
        image_hash: str,
        source: str = "unknown",
    ) -> None:
        """Register a token logo hash."""
        self._logos[token_address.lower()] = {
            "symbol": token_symbol,
            "address": token_address,
            "chain": chain,
            "hash": image_hash,
            "source": source,
        }

    def find_similar(self, image_hash: str, threshold: float = 0.85) -> list[dict[str, Any]]:
        """Find logos similar to the given hash. Returns matches above threshold."""
        matches = []
        for _addr, logo in self._logos.items():
            sim = compare_image_hashes(image_hash, logo["hash"])
            if sim >= threshold:
                matches.append({**logo, "similarity": round(sim, 3)})
        matches.sort(key=lambda m: m["similarity"], reverse=True)
        return matches

    def check_theft(self, image_hash: str) -> dict[str, Any]:
        """
        Check if a token logo appears to be stolen from a known project.

        Returns analysis with confidence and matched original projects.
        """
        matches = self.find_similar(image_hash, threshold=0.90)
        very_high = [m for m in matches if m["similarity"] >= 0.95]
        high = [m for m in matches if 0.90 <= m["similarity"] < 0.95]

        if very_high:
            return {
                "suspected_theft": True,
                "confidence": "high",
                "matched_logos": very_high,
                "summary": f"Logo appears stolen from {very_high[0]['symbol']} "
                f"({very_high[0]['chain']}) - {very_high[0]['similarity']:.1%} similarity",
            }
        elif high:
            return {
                "suspected_theft": True,
                "confidence": "medium",
                "matched_logos": high,
                "summary": f"Logo similar to {high[0]['symbol']} "
                f"({high[0]['chain']}) - {high[0]['similarity']:.1%} similarity",
            }
        return {
            "suspected_theft": False,
            "confidence": "none",
            "matched_logos": matches,
            "summary": "No stolen artwork detected",
        }


# ── Image Description (via DeepSeek Vision) ──────────────────────


async def describe_image(
    image_url: str = "",
    image_base64: str = "",
    task: str = "describe",
) -> str:
    """
    Generate a text description of an image using DeepSeek v4 Flash vision.

    Uses the existing LLM_API_KEY configuration.

    task: "describe" | "extract_addresses" | "analyze_chart" | "detect_scam"
    """
    try:
        from rugbrain.compat import AI_BASE, LLM_API_KEY  # external dependency

        if not LLM_API_KEY:
            logger.warning("No LLM key available for image description")
            return "Image description unavailable - no LLM API key configured"

        TASK_PROMPTS = {  # noqa: N806 - dict literal in function, uppercase intentional for visibility
            "describe": "Describe this image in detail. What crypto-related content does it show?",
            "extract_addresses": (
                "Extract any cryptocurrency wallet addresses (0x... for EVM, "
                "base58 for Solana, etc.), contract addresses, transaction hashes, "
                "and token symbols visible in this image. Return them as a JSON list "
                'with format: {"addresses": [...], "symbols": [...], "chains": [...]}'
            ),
            "analyze_chart": (
                "Analyze this price chart. Does it show signs of a pump-and-dump, "
                "rug pull, or wash trading? Look for: sudden spikes, immediate crashes, "
                "flat volume then massive spike, stair-step patterns."
            ),
            "detect_scam": (
                "Is this screenshot showing a potential crypto scam? Check for: "
                "fake UI elements, too-good-to-be-true returns, impersonation of "
                "legitimate platforms, urgency tactics, grammar errors."
            ),
        }

        prompt = TASK_PROMPTS.get(task, TASK_PROMPTS["describe"])
        payload: dict[str, Any] = {
            "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
        }

        if image_url:
            payload["messages"][0]["content"] = [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": prompt},
            ]
        elif image_base64:
            payload["messages"][0]["content"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                },
                {"type": "text", "text": prompt},
            ]
        else:
            return "No image provided"

        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                AI_BASE,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                return cast(str, data["choices"][0]["message"]["content"])
            else:
                logger.warning("Image description failed: HTTP %s", resp.status_code)
                return f"Image analysis error: HTTP {resp.status_code}"

    except ImportError:
        logger.debug("LLM module not available for vision")
        return "Vision analysis unavailable"
    except Exception as e:
        logger.warning("Image description error: %s", e)
        return f"Image analysis failed: {e}"


# ── Screenshot OCR - lightweight text extraction ─────────────────


def extract_text_from_image_hints(image_bytes: bytes) -> dict[str, list[str]]:
    """
    Lightweight heuristic text extraction from image bytes.
    Searches for crypto address patterns, URLs, and keywords.

    For full OCR, use describe_image(task='extract_addresses') with DeepSeek vision.
    """
    text = ""
    try:
        # Try to extract printable ASCII from the raw bytes
        text = "".join(chr(b) for b in image_bytes if 32 <= b < 127)
        # Limit to first 10K chars
        text = text[:10000]
    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    results: dict[str, Any] = {
        "eth_addresses": [],
        "sol_addresses": [],
        "urls": [],
        "amounts": [],
    }

    import re

    # ETH addresses
    eth_matches = re.findall(r"0x[a-fA-F0-9]{40}", text)
    results["eth_addresses"] = list(set(eth_matches))[:20]

    # Solana addresses
    sol_matches = re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", text)
    results["sol_addresses"] = list(set(sol_matches))[:20]

    # URLs
    url_matches = re.findall(r"https?://[^\s]+", text)
    results["urls"] = list(set(url_matches))[:10]

    # Dollar amounts
    amount_matches = re.findall(r"\$[\d,]+(?:\.\d+)?", text)
    results["amounts"] = amount_matches[:10]

    return results


# ── Singleton ─────────────────────────────────────────────────────

_logo_db: TokenLogoDB | None = None


def get_logo_db() -> TokenLogoDB:
    global _logo_db
    if _logo_db is None:
        _logo_db = TokenLogoDB()
    return _logo_db
