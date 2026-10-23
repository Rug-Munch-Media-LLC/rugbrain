"""Is this joined channel a scam / shill / honeypot / promotion channel?

Discovery runs over the account's dialog list, which is 219 dialogs of mixed
signal: news outlets, protocol channels, call channels, and the "PORTAL /
ANNOUNCEMENTS / AD" shell channels that shill a token or route traffic. Only
the last three groups are worth sweeping as scam-adjacent intel.

The taxonomy (:mod:`channel_taxonomy`) already tags ``shiller`` and
``scam_caller``; this module adds the two classes it deliberately does not own:

* ``promotion`` — name-only shell channels (``… Portal``, ``… Announcements``,
  ``Traffic Ad``). Telegram's own naming convention for these, and the reason
  a word-bounded match is enough.
* honeypot/drainer/rug wording that the taxonomy folds into ``shiller`` but
  which is really a scam-caller signal.

Never matches the owner's own channels: a self-promo must not be scored as a
scam call-out.
"""

from __future__ import annotations

from rugbrain.ingest_sources.channel_naming import fold_channel_name, hit
from rugbrain.ingest_sources.channel_taxonomy import categorize

#: Handle prefixes the fleet owns — ingesting them as "scam intel" would
#: poison the score, and a prefix match survives the ``crmunchers`` /
#: ``munchscans`` / ``rmialerts`` variants the same brand ships under.
OWN_PREFIXES: tuple[str, ...] = (
    "cryptorugmunch",
    "crmunch",
    "munchscan",
    "rmialert",
    "rmialpha",
    "rugmunch",
)

#: Official exchange / protocol announcement channels. They match the shell
#: naming but are primary news sources, not affiliate shilling.
LEGIT_ANNOUNCEMENTS: frozenset[str] = frozenset(
    {"arbitrum", "binance_announcements", "celestiaorg", "spotonchain"}
)

#: Signals of a rug/honeypot call-out channel, matched word-bounded.
HONEYPOT_NEEDLES: tuple[str, ...] = (
    "rug",
    "rugs",
    "honeypot",
    "scam",
    "scammer",
    "scammers",
    "phishing",
    "drainer",
)

#: Telegram's shell-channel conventions — a token portal or an ad feed.
PROMO_NEEDLES: tuple[str, ...] = (
    "portal",
    "announcement",
    "announcements",
    "notifier",
    "promo",
    "giveaway",
    "presale",
    "casino",
    "traffic ad",
    "ad",
)

#: Taxonomy categories that are already scam-adjacent without extra rules.
TAXONOMY_HITS: frozenset[str] = frozenset({"scam_caller", "shiller", "security"})


def classify_joined(title: str, username: str) -> str | None:
    """Return ``scam_caller``/``shiller``/``security``/``promotion``, else None.

    Ordered deliberately: the taxonomy (which owns brands such as
    ``CertiKAlert``) wins first, then honeypot wording, then shell-channel
    naming. A news outlet that merely mentions "rug" in its title does not
    exist as a real channel, so no guard is needed for that case.
    """
    handle = (username or "").strip().lstrip("@").lower()
    if not handle or handle.startswith(OWN_PREFIXES) or handle in LEGIT_ANNOUNCEMENTS:
        return None
    base = categorize(title, username)
    if base in TAXONOMY_HITS:
        return base
    folded = fold_channel_name(title, handle)
    tokens = frozenset(folded.split())
    if any(hit(n, tokens, folded) for n in HONEYPOT_NEEDLES):
        return "scam_caller"
    if any(hit(n, tokens, folded) for n in PROMO_NEEDLES):
        return "promotion"
    return None


__all__ = [
    "HONEYPOT_NEEDLES",
    "LEGIT_ANNOUNCEMENTS",
    "OWN_PREFIXES",
    "PROMO_NEEDLES",
    "classify_joined",
]
