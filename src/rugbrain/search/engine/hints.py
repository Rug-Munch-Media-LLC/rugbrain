"""
RAG Search Engine — query-hint vocabularies and code-term regexes.

Phrase hints and word-boundary regexes used by ``query_intent`` to classify
scam/exploit vs smart-contract coding queries.
"""

import re

# Keywords that strongly indicate a smart-contract / coding question.
_CODING_QUERY_HINTS = (
    # Languages / ecosystems.
    "solidity",
    "smart contract",
    "smart contracts",
    "erc20",
    "erc721",
    "erc1155",
    "erc4626",
    "erc-20",
    "erc-721",
    "openzeppelin",
    "yul",
    "evm",
    "abi encoding",
    "abi.decode",
    "abi.encode",
    "storage layout",
    "gas optimization",
    "solana",
    "anchor framework",
    "anchor program",
    "program derived address",
    "pda",
    "cpi",
    "cross-program",
    "spl token",
    "token program",
    "associated token",
    "rent exemption",
    "system program",
    "bpf",
    "move language",
    "aptos",
    "sui",
    "cairo",
    "starknet",
    "starknet contract",
    # Intent markers.
    "how to implement",
    "how to write",
    "how to build",
    "how to code",
    "code example",
    "code snippet",
    "in solidity",
    "in rust",
    "in move",
    "in cairo",
    "smart contract development",
    "contract development",
    "reentrancy guard",
    "nonreentrant",
    "checks-effects-interactions",
    "secure erc",
    "secure token",
    "secure contract",
    "contract best practice",
    "resource model",
    "resource-oriented",
    "formal verification",
)

# Keywords that strongly indicate a scam/exploit/intel query. Matching one
# routes the search to SCAM_COLLECTIONS (in addition to any explicit list).
_SCAM_QUERY_HINTS = (
    "hack",
    "hacked",
    "exploit",
    "vulnerab",
    "attack",
    "attacker",
    "rug pull",
    "rugpull",
    "scam",
    "scammer",
    "phishing",
    "phish",
    "fraud",
    "stolen",
    "theft",
    "heist",
    "rekt",
    "breach",
    "vuln",
    "cve-",
    "swc-",
    "flash loan",
    "reentrancy",
    "oracle manipulation",
    "lazarus",
    "siphone",
    "drainer",
    "exploiter",
    "exploit code",
    "poc",
    "proof of concept",
    "how was .* hacked",
)

# ── Code-term regex tier (second detection layer) ──────────────────────
# ``detect_coding_query`` checks the phrase hints above first, then these
# word-boundary regexes so code-intent queries that do not contain a hint
# phrase verbatim ("what is the ABI", "function selector", "write a Python
# SDK wrapper", "how does reentrancy work") still route to code retrieval.
# Strong terms are unambiguous code vocabulary; contextual terms ("code",
# "example", "reentrancy", …) only count when paired with a second code term
# or a "how does" phrasing so exploit-framed queries ("reentrancy hack 2023",
# "exploit code") keep routing to scam/exploit knowledge.
_CODING_TERMS_STRONG = (
    r"solidity|smart\s?contract|contracts?|erc\d{3,4}|openzeppelin|yul|vyper|bytecode|opcode|"
    r"solana|anchor|spl\s?token|associated\s?token|rent\s?exemption|system\s?program|"
    r"program\s?derived\s?address|\bpda\b|\bcpi\b|\bbpf\b|"
    r"move\s?language|aptos|sui|cairo|starknet|rust|python|typescript|javascript|golang|"
    r"\babi\b|\bmodifier\b|\bfunction\b|\bsdk\b|\bsnippet\b|\bsyntax\b|compil(?:e|er|ation)|"
    r"storage\s?layout|gas\s?optimization|formal\s?verification|checks[- ]effects|"
    r"nonreentrant|reentrancy\s?guard|\bmev\b|\bconstructor\b|abi\s?encoding"
)
_CODING_TERMS_CONTEXTUAL = (
    r"interface|library|struct|enum|event|class|def|impl|trait|fn|mod|"
    r"code|example|implementation|program|bot|deploy|script|reentrancy|\bapi\b"
)
_CODING_TERM_RE = re.compile(rf"\b(?:{_CODING_TERMS_STRONG})\b")
_CODING_CTX_RE = re.compile(rf"\b(?:{_CODING_TERMS_CONTEXTUAL})\b")
_HOW_DOES_RE = re.compile(r"\bhow (?:do|does|is|are|can)\b")
