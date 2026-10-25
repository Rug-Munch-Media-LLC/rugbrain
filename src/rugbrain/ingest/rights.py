"""Storage-rights ladder — what we may PERSIST per source.

The ladder limits **what we keep**, not what we may read. Internal use on
our own server widens retention for sources that allow it; it never erases
a source's own licence or an explicit anti-scraping clause. The binding
constraint is the *source's* terms, not ours.

Four tiers (see ``docs/CORPUS-RIGHTS.md``):

  * ``FULL_TEXT``    — licence permits storage (MIT/Apache/CC-BY/CC-BY-SA/
    public domain): whole documents + licence field.
  * ``EXCERPT_CITE`` — no explicit licence but publicly readable: short
    excerpt (<=400 chars) + title/url/date/class/severity/hash. Never a
    full-text mirror; always cite and link.
  * ``POINTER_ONLY`` — terms forbid scraping/copying: metadata + facts we
    derive ourselves only. No body text at all.
  * ``REJECT``       — credentials/paywall/login required, or automated
    access explicitly prohibited: do not fetch.

Declarations are keyed by source **class name** so the guard cannot miss a
source through an inconsistent ``name`` attribute.
"""

from __future__ import annotations

from enum import StrEnum

#: Character budget for ``EXCERPT_CITE`` bodies.
EXCERPT_MAX_CHARS = 400

#: Metadata keys permitted for ``POINTER_ONLY`` (no body text, ever).
POINTER_METADATA_ALLOW = frozenset(
    {
        "title", "url", "published_at", "date", "category", "severity",
        "doc_type", "source", "chain", "amount_usd", "tx_hash", "address",
        "tags", "licence", "content_hash", "derived",
    }
)


class StorageRight(StrEnum):
    """The four storage tiers, most permissive first."""

    FULL_TEXT = "full_text"
    EXCERPT_CITE = "excerpt_cite"
    POINTER_ONLY = "pointer_only"
    REJECT = "reject"


class RightRefused(PermissionError):
    """Raised when a source may not be fetched or stored at all."""


class UndeclaredRight(RuntimeError):
    """Raised when a source has no declared storage right."""


F, E, P, R = (StorageRight.FULL_TEXT, StorageRight.EXCERPT_CITE,
              StorageRight.POINTER_ONLY, StorageRight.REJECT)

#: source class name -> tier. Reclassified 2026-10-25 per CORPUS-RIGHTS.md.
DECLARATIONS: dict[str, StorageRight] = {
    # -- full_text: explicit permissive licence --------------------------
    "DeFiHackLabsSource": F,          # MIT
    "EIPsSource": F,                  # CC0
    "BIPsSource": F,                  # CC0
    "OpenZeppelinSource": F,          # MIT
    "YAuditForkBugsSource": F,        # MIT
    "CoinspectEVMAttacksSource": F,   # MIT
    "FortaMaliciousContractsSource": F,  # MIT
    "SafeSource": F,                  # LGPL-3.0
    "TornadoSource": F,               # GPL-3.0
    "CircomSource": F,                # GPL-3.0
    "SnarkJSSource": F,               # GPL-3.0
    "OSSFMaliciousPackagesSource": F,  # Apache-2.0
    "URLHausSource": F,               # CC0 feed
    "OpenPhishSource": F,             # free-use feed
    "CISAKevSource": F,               # US gov public domain
    "EmperorMewCFTCSource": F,        # US gov public domain
    "DeFiLlamaSource": F,             # open data / CC0
    "DefiLlamaDocsSource": F,         # MIT docs
    "DevDocsSource": F,               # CC-BY-4.0 specs/MDN
    "VulnerabilitySeveritySource": F,
    "FraudCoinsSignaturesSource": F,  # derived signatures
    # Trail of Bits publications: CC-BY-SA-4.0 -> store, internal-only,
    # attribution + share-alike honoured (never republished).
    "TrailOfBitsPublicationsSource": F,
    # -- excerpt_cite: public, no explicit licence -----------------------
    "SWCSource": E, "AnalyticsDocsSource": E, "WhitepapersSource": E,
    "BotLaunchpadDocsSource": E, "L2BeatSource": E, "LayerZeroSource": E,
    "AxelarSource": E, "CCIPSource": E, "MEVBoostSource": E,
    "MEVBoostRelaySource": E, "BlacklistsSource": E, "PhishDestroySource": E,
    "GutteDeFiSource": E, "BlockSecDefiPocSource": E,
    "SlowMistOpenReportsSource": E, "Seal911Source": E, "SamczsunSource": E,
    "BlockSecPhishingContractsSource": E, "TrailOfBitsBlogSource": E,
    "ZellicBlogSource": E, "ImmunefiBlogSource": E, "NeodymeBlogSource": E,
    "CFTCPressReleasesSource": E, "ScytheRadarDatasetsSource": E,
    "CRPWarnerRugEventsSource": E, "EngineeringBlogsSource": E,
    "A16ZCryptoSource": E, "FlashbotsWritingsSource": E, "BaseDocsSource": E,
    "ArbitrumDocsSource": E, "GCF3711AuditArchiveSource": E,
    "DeFiLabXSolRPDSource": E,
    # Code4rena: no explicit licence, publicly readable -> excerpt only.
    # Solodit/Cyfrin/Spearbit upstreams are pointer_only (see docs).
    "PublicAuditFindingsSource": E,
    # -- pointer_only: terms forbid scraping/mirroring -------------------
    "RektSource": P,               # rekt.news: no licence, full mirror refused
    "CryptoRektsSource": P,        # mirror of the same corpus
    "ImmunefiHackAnalysesSource": P,  # ToS: no bulk copy
    "ChainabuseSource": P,         # ToS: report text not redistributable
    "ChainalysisInsightsSource": P,   # commercial ToS
    "OpenSanctionsSource": P,      # CC-BY-NC: non-commercial only
    "MarketHealthSource": P,       # CoinGecko ToS: derived facts only
    # -- reject: do not fetch --------------------------------------------
}

def require_right(source: object) -> StorageRight:
    """Return the storage tier for *source* or raise.

    Accepts a source instance or a class name string. Refuses ``REJECT``
    sources outright so the ingest path fails closed.
    """
    key = source if isinstance(source, str) else type(source).__name__
    right: StorageRight | None = DECLARATIONS.get(key)
    if right is None:
        raise UndeclaredRight(f"no storage right declared for {key!r}")
    if right is StorageRight.REJECT:
        raise RightRefused(f"{key} may not be fetched (tier=reject)")
    return right


__all__ = ["DECLARATIONS", "EXCERPT_MAX_CHARS",
           "POINTER_METADATA_ALLOW", "RightRefused", "StorageRight",
           "UndeclaredRight", "require_right"]
