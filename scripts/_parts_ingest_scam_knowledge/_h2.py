"""Header chunk 2/2 of scripts/ingest_scam_knowledge.py (imports + constants)."""

from _parts_ingest_scam_knowledge._h1 import *  # noqa: F403

_CONTENT_VECTORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"reentranc", re.I), "reentrancy"),
    (re.compile(r"flash\s?loan", re.I), "flash-loan"),
    (re.compile(r"oracle", re.I), "oracle-manipulation"),
    (re.compile(r"price\s?manipulation", re.I), "oracle-manipulation"),
    (re.compile(r"access\s?control", re.I), "access-control"),
    (re.compile(r"onlyowner|admin\s?privilege", re.I), "access-control"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"signature", re.I), "signature-malleability"),
    (re.compile(r"overflow|underflow", re.I), "integer-overflow"),
    (re.compile(r"rug\s?pull|rugpull", re.I), "rug-pull"),
    (re.compile(r"honeypot", re.I), "honeypot"),
    (re.compile(r"phish", re.I), "phishing"),
    (re.compile(r"private\s?key|seed\s?phrase", re.I), "key-compromise"),
    (re.compile(r"withdraw", re.I), "withdrawal-exploit"),
    (re.compile(r"denial\s?of\s?service|dos\b", re.I), "denial-of-service"),
    (re.compile(r"front\s?run", re.I), "front-running"),
    (re.compile(r"supply\s?chain", re.I), "supply-chain"),
    (re.compile(r"input\s?valid", re.I), "input-validation"),
]

_CONTRACT_RE = re.compile(r"\bcontract\s+(\w+)")

_COMMENT_LINES_RE = re.compile(r"^\s*(//|/\*|\*)\s*(.*)$", re.MULTILINE)

_SKIP_SOL = {"interface.sol", "basetest.sol", "tokenhelper.sol", "StableMath.sol"}

REKT_LEADERBOARD_URL = "https://rekt.news/leaderboard"

_YAML_KEY_RE = re.compile(r"^\s*name:\s*(.+)$")

_YAML_URL_RE = re.compile(r"^\s*url:\s*(.+)$")

_YAML_CAT_RE = re.compile(r"^\s*category:\s*(.+)$")

_YAML_SUB_RE = re.compile(r"^\s*subcategory:\s*(.+)$")

_YAML_DESC_RE = re.compile(r"^\s*description:\s*(.+)$")

_YAML_ADDR_RE = re.compile(r'^\s*-\s*"?(0x[a-fA-F0-9]{20,})"?\s*$')

_SLOWMIST_MD_FILES = (
    (
        "blockchain_security_study_notes",
        "https://github.com/slowmist/Knowledge-Base/blob/master/blockchain_security_study_notes/{f}",
    ),
    ("translations", "https://github.com/slowmist/Knowledge-Base/blob/master/translations/{f}"),
    ("open-report-V2", "https://github.com/slowmist/Knowledge-Base/blob/master/open-report-V2/{f}"),
)

_SWC_MD = "https://raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/entries/docs/{swc}.md"

_SWC_IDS = [f"SWC-{i}" for i in range(100, 137)]

_SWC_VECTORS: dict[str, str] = {
    "SWC-100": "access-control",
    "SWC-101": "integer-overflow",
    "SWC-102": "supply-chain",
    "SWC-103": "supply-chain",
    "SWC-104": "input-validation",
    "SWC-105": "access-control",
    "SWC-106": "access-control",
    "SWC-107": "reentrancy",
    "SWC-108": "access-control",
    "SWC-109": "smart-contract-exploit",
    "SWC-110": "input-validation",
    "SWC-111": "supply-chain",
    "SWC-112": "access-control",
    "SWC-113": "denial-of-service",
    "SWC-114": "front-running",
    "SWC-115": "access-control",
    "SWC-116": "oracle-manipulation",
    "SWC-117": "signature-malleability",
    "SWC-118": "access-control",
    "SWC-119": "smart-contract-exploit",
    "SWC-120": "oracle-manipulation",
    "SWC-121": "signature-malleability",
    "SWC-122": "smart-contract-exploit",
    "SWC-123": "input-validation",
    "SWC-124": "smart-contract-exploit",
    "SWC-125": "smart-contract-exploit",
    "SWC-126": "denial-of-service",
    "SWC-127": "smart-contract-exploit",
    "SWC-128": "denial-of-service",
    "SWC-129": "smart-contract-exploit",
    "SWC-130": "input-validation",
    "SWC-131": "smart-contract-exploit",
    "SWC-132": "smart-contract-exploit",
    "SWC-133": "signature-malleability",
    "SWC-134": "smart-contract-exploit",
    "SWC-135": "smart-contract-exploit",
    "SWC-136": "supply-chain",
}

DEFILLAMA_HACKS_API = "https://api.llama.fi/hacks"

IMMUNEFI_EXPLORE_URL = "https://immunefi.com/explore/"

_IMMUNEFI_STOPWORDS = {
    "bounty",
    "rewards",
    "overview",
    "audit",
    "live",
    "launch",
    "view all",
    "read more",
    "loading",
    "explore",
    "immunefi",
    "sign in",
    "get started",
    "dashboard",
}

SOLODIT_URL = "https://solodit.xyz"

SOURCE_TABLE: dict[str, tuple[str, str, tuple[str, ...] | None]] = {
    # name → (repo URL, local dir name, subdirs to keep)
    "defihacklabs": (
        "https://github.com/SunWeb3Sec/DeFiHackLabs.git",
        "defihacklabs",
        ("src", "past"),
    ),
    "cryptoscamdb": ("https://github.com/CryptoScamDB/blacklist.git", "blacklist", ("data",)),
    "slowmist": ("https://github.com/slowmist/Knowledge-Base.git", "slowmist", None),
    "web3hacks": (
        "https://github.com/Cyfrin/sc-exploits-minimized.git",
        "cyfrin-exploits",
        ("src",),
    ),
}


__all__ = [
    "DEFILLAMA_HACKS_API",
    "IMMUNEFI_EXPLORE_URL",
    "REKT_LEADERBOARD_URL",
    "SOLODIT_URL",
    "SOURCE_TABLE",
    "_COMMENT_LINES_RE",
    "_CONTENT_VECTORS",
    "_CONTRACT_RE",
    "_IMMUNEFI_STOPWORDS",
    "_SKIP_SOL",
    "_SLOWMIST_MD_FILES",
    "_SWC_IDS",
    "_SWC_MD",
    "_SWC_VECTORS",
    "_YAML_ADDR_RE",
    "_YAML_CAT_RE",
    "_YAML_DESC_RE",
    "_YAML_KEY_RE",
    "_YAML_SUB_RE",
    "_YAML_URL_RE",
]
