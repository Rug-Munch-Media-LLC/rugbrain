"""
RAGAS Evaluation Framework for RugBrain
=========================================
Measures RAG quality with crypto-specific golden test set.

Metrics:
  - context_precision (nDCG@k)
  - context_recall (% relevant docs found)
  - faithfulness (answer claims supported by context)
  - answer_relevancy (cosine sim between answer and query embeddings)
  - hit_rate (% queries with at least one relevant hit)
"""

# NOTE (deprecated): archived in _legacy/ — kept for reference only, has zero
# live references, and is not type-checked in CI. Per-line pyright ignores
# below silence diagnostics that would otherwise require porting private
# monorepo dependencies that do not exist in the OSS rugbrain package.

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Per-query budget: every search call is wrapped in ``asyncio.wait_for`` with
# this timeout so a hung retrieval (cold embed, wedged event loop) cannot
# stall the full evaluation run for minutes at a time.
EVAL_TIMEOUT_S = 10.0
EVAL_MAX_CONCURRENCY = 5


# ======================================================================
# GOLDEN TEST SET - 50+ (query, relevant_doc_ids, expected_answer) pairs
# ======================================================================

GOLDEN_TEST_SET = [
    # -- wallet_profiles --
    {
        "query": "Is 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D a known scam address?",
        "relevant_doc_ids": ["known_scams_router_v2", "scam_uniswap_router"],
        "expected_answer": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D is the Uniswap V2 Router 2 address. While the address itself is legitimate, scammers often create fake tokens that use this router. The address alone does not indicate a scam.",
        "collection": "scam_intel",
    },
    {
        "query": "What wallet labels indicate high risk on Solana?",
        "relevant_doc_ids": ["sol_rug_puller_label", "sol_honeypot_creator"],
        "expected_answer": "High-risk wallet labels on Solana include rug_puller, honeypot_creator, wash_trader, mixer_user, and exploiter. These labels are assigned based on on-chain behavioral analysis.",
        "collection": "scam_intel",
    },
    {
        "query": "How to identify a wash trading wallet on Ethereum?",
        "relevant_doc_ids": ["eth_wash_trade_pattern", "wash_trading_indicators"],
        "expected_answer": "Wash trading wallets exhibit circular transaction patterns, high volume but low unique counterparties, self-trading between connected wallets, and unnatural trading frequency.",
        "collection": "scam_intel",
    },
    {
        "query": "Which wallets are flagged as sanctioned on BSC?",
        "relevant_doc_ids": ["bsc_sanctioned_list", "sanctioned_wallets_bsc"],
        "expected_answer": "Sanctioned wallets on BSC are those flagged by OFAC or other regulatory bodies. They typically show interactions with mixers, darknet markets, or ransomware payments.",
        "collection": "scam_intel",
    },
    {
        "query": "What behavioral patterns indicate a sniper bot wallet?",
        "relevant_doc_ids": ["sniper_bot_indicators", "bundle_sniper_pattern"],
        "expected_answer": "Sniper bot wallets buy tokens within the same block as token creation, use high gas priority fees, and often sell within minutes for quick profit.",
        "collection": "scam_intel",
    },
    # -- token_analysis --
    {
        "query": "What are indicators of a honeypot token?",
        "relevant_doc_ids": ["honeypot_sell_disabled", "honeypot_indicators"],
        "expected_answer": "Honeypot tokens prevent selling through mechanisms like maxSellAmount=0, tradingEnabled=false, or blacklisting all non-owner addresses. Indicators include inability to sell after buying, high buy tax but 100% sell tax, and trading restrictions.",
        "collection": "scam_intel",
    },
    {
        "query": "Is a token with 99% sell tax a scam?",
        "relevant_doc_ids": ["hidden_fee_manipulation", "fee_scam_pattern"],
        "expected_answer": "A token with 99% sell tax is almost certainly a scam. This is the fee manipulation pattern where the owner sets taxes to 100% after launch, making sells impossible or stealing all transfer value.",
        "collection": "scam_intel",
    },
    {
        "query": "How does the Uniswap V2 router rug pull work?",
        "relevant_doc_ids": ["liquidity_drain_backdoor", "uniswap_rug_mechanism"],
        "expected_answer": "Uniswap V2 rug pulls work when the token contract contains a hidden function that calls router.removeLiquidity, allowing the owner to drain the LP pool. This is often disguised as manualSwap or rescueToken functions.",
        "collection": "scam_intel",
    },
    {
        "query": "What token metadata suggests a proxy upgrade trap?",
        "relevant_doc_ids": ["proxy_upgrade_trap", "upgrade_scam_pattern"],
        "expected_answer": "Proxy upgrade traps use UUPS or TransparentUpgradeableProxy patterns where the admin can change the implementation contract post-launch to a malicious version. Indicators include upgradeTo functions, admin change capabilities, and no timelock on upgrades.",
        "collection": "scam_intel",
    },
    {
        "query": "How to detect unlimited mint vulnerability in a token?",
        "relevant_doc_ids": ["unlimited_mint_rug", "mint_vulnerability"],
        "expected_answer": "Unlimited mint vulnerabilities exist when a token has a mint function callable by the owner without supply cap restrictions. The owner can mint millions of tokens, diluting holders to zero before draining liquidity.",
        "collection": "scam_intel",
    },
    # -- scam_patterns --
    {
        "query": "What is the bundle sniper launch scam pattern?",
        "relevant_doc_ids": ["bundle_sniper_launch", "bundled_launch_pattern"],
        "expected_answer": "Bundle sniper launch is when the creator uses bundling to acquire large token supply at launch before others can buy. The same funder finances multiple wallets that buy in block 0, accumulating 60%+ of supply, then dumps.",
        "collection": "scam_intel",
    },
    {
        "query": "How do fake renounce ownership scams work?",
        "relevant_doc_ids": ["fake_renounce_pattern", "ownership_hide_scam"],
        "expected_answer": "Fake renounce scams claim ownership is renounced but keep control via a secondary address, proxy contract, or timelock trick. The ownership is not actually transferred to 0xdead and renounceOwnership is not truly called.",
        "collection": "scam_intel",
    },
    {
        "query": "What are the most critical scam patterns to monitor?",
        "relevant_doc_ids": [
            "honeypot_sell_disabled",
            "unlimited_mint_rug",
            "liquidity_drain_backdoor",
            "wallet_drainer_approval",
        ],
        "expected_answer": "The most critical scam patterns are: honeypot with sell disabled, unlimited mint rug pull, liquidity drain backdoor, and wallet drainer/approval scams. These all result in total loss of funds for victims.",
        "collection": "scam_intel",
    },
    {
        "query": "How does a sandwich bot tax farm operate?",
        "relevant_doc_ids": ["sandwich_bot_tax_farm", "tax_farm_mev_pattern"],
        "expected_answer": "A sandwich bot tax farm uses tokens with high taxes (>10%) where the creator also runs MEV sandwich bots. The bots front-run and back-run victim transactions, extracting value from traders while the tax mechanism amplifies the extractable value.",
        "collection": "scam_intel",
    },
    {
        "query": "What code patterns indicate a wallet drainer contract?",
        "relevant_doc_ids": ["wallet_drainer_approval", "drainer_code_patterns"],
        "expected_answer": "Wallet drainer contracts trick users into approving token spending then drain wallets via transferFrom. Code patterns include unlimited approval requests, transferFrom in unverified contracts, approveAndCall, and permit exploit functions.",
        "collection": "scam_intel",
    },
    # -- forensic_reports --
    {
        "query": "What was the mechanism behind the Merlin DEX exploit?",
        "relevant_doc_ids": ["merlin_dex_forensic", "merlin_exploit_report"],
        "expected_answer": "The Merlin DEX exploit involved a rogue auditor who embedded a backdoor in the smart contract that allowed draining of liquidity pools shortly after launch.",
        "collection": "hacks",
    },
    {
        "query": "How was the Wormhole bridge hack executed?",
        "relevant_doc_ids": ["wormhole_forensic", "bridge_exploit_wormhole"],
        "expected_answer": "The Wormhole bridge hack exploited a signature verification vulnerability in the bridge contract, allowing the attacker to mint 120k ETH on Solana without proper backing on Ethereum.",
        "collection": "hacks",
    },
    {
        "query": "What forensic evidence links rug pulls to deployer wallets?",
        "relevant_doc_ids": ["deployer_forensic_link", "rug_deployer_evidence"],
        "expected_answer": "Forensic evidence linking rug pulls includes: same deployer address across multiple scam tokens, funded wallets that interact with the token contract, liquidity removal from the deployer wallet, and timing correlation between deployer actions and price collapse.",
        "collection": "hacks",
    },
    {
        "query": "How do investigators trace stolen funds through mixers?",
        "relevant_doc_ids": ["mixer_tracing_forensic", "fund_flow_analysis"],
        "expected_answer": "Investigators trace stolen funds through mixers using heuristics like timing analysis, amount correlation, graph analysis of transaction flows, and clustering of input/output wallets. Advanced techniques include dust tracing and exchange deposit matching.",
        "collection": "hacks",
    },
    {
        "query": "What chain analysis techniques detect wash trading on DEXs?",
        "relevant_doc_ids": ["wash_trade_forensic", "dex_wash_analysis"],
        "expected_answer": "Chain analysis techniques for wash trading detection include circular flow detection, self-trade identification via shared funding sources, volume anomaly analysis, and counterparty diversity scoring.",
        "collection": "hacks",
    },
    # -- market_intel --
    {
        "query": "What market signals precede a rug pull?",
        "relevant_doc_ids": ["rug_pull_market_signals", "pre_rug_indicators"],
        "expected_answer": "Market signals preceding rug pulls include sudden liquidity removal, large holder concentration increasing, social media hype coupled with declining liquidity, trading volume spikes without corresponding price movement, and new token launches by known bad actors.",
        "collection": "news",
    },
    {
        "query": "How does social media sentiment correlate with pump and dump schemes?",
        "relevant_doc_ids": ["pump_dump_sentiment", "social_media_scam_signals"],
        "expected_answer": "Pump and dump schemes show a pattern of coordinated social media hype followed by synchronized selling. Sentiment peaks correlate with price peaks, and negative sentiment spikes appear after the dump phase.",
        "collection": "news",
    },
    {
        "query": "What trading volume patterns indicate artificial manipulation?",
        "relevant_doc_ids": ["volume_manipulation_market", "artificial_volume_signals"],
        "expected_answer": "Artificial volume manipulation shows sudden volume spikes not correlated with price discovery, repetitive round-number transactions, high volume with low price impact, and volume concentrated in brief time windows.",
        "collection": "news",
    },
    # -- contract_audits --
    {
        "query": "What Solidity patterns indicate a hidden owner function?",
        "relevant_doc_ids": ["hidden_owner_audit", "proxy_audit_finding"],
        "expected_answer": "Hidden owner functions in Solidity include delegatecall to user-supplied addresses, upgradeable proxy patterns without timelock, multiple ownership roles where one is hidden, and assembly-level ownership checks that bypass standard modifiers.",
        "collection": "threat_intel",
    },
    {
        "query": "How to audit a token contract for reentrancy vulnerabilities?",
        "relevant_doc_ids": ["reentrancy_audit_guide", "contract_audit_reentrancy"],
        "expected_answer": "Reentrancy audit checks include: verifying checks-effects-interactions pattern, identifying external calls before state updates, checking for fallback function exploits, analyzing delegatecall patterns, and verifying reentrancy guards are in place.",
        "collection": "threat_intel",
    },
    {
        "query": "What access control issues appear in rug pull contracts?",
        "relevant_doc_ids": ["access_control_rug", "onlyOwner_audit_issues"],
        "expected_answer": "Access control issues in rug pull contracts include: onlyOwner modifier guarding dangerous functions, missing or fake renounceOwnership, time-locked functions with short delays, and multi-sig requirements that are actually single-sig.",
        "collection": "threat_intel",
    },
    {
        "query": "How does Slither detect vulnerability patterns in smart contracts?",
        "relevant_doc_ids": ["slither_audit_patterns", "static_analysis_detection"],
        "expected_answer": "Slither uses static analysis to detect vulnerability patterns including reentrancy, unchecked return values, access control issues, and dangerous delegatecall usage. It performs taint analysis and control flow graph construction.",
        "collection": "threat_intel",
    },
    # -- known_scams --
    {
        "query": "Is the Squid Game token a known scam?",
        "relevant_doc_ids": ["squid_game_scam", "known_scams_squid"],
        "expected_answer": "Yes, the Squid Game token was a known rug pull scam. The token price rose dramatically then the developers drained liquidity and disappeared. It featured honeypot mechanics that prevented selling.",
        "collection": "scam_intel",
    },
    {
        "query": "What are the top 5 known DeFi rug pulls by loss amount?",
        "relevant_doc_ids": ["defi_rug_pulls_top", "known_scams_largest"],
        "expected_answer": "The top DeFi rug pulls by loss include: Thodex ($2B), OneCoin ($4B), PlusToken ($2.9B), Bitconnect ($1B), and AnubisDAO ($60M). These scams combined ponzi mechanics, liquidity draining, and exit scams.",
        "collection": "scam_intel",
    },
    {
        "query": "How do known wallet drainer phishing sites operate?",
        "relevant_doc_ids": ["wallet_drainer_phishing", "known_drainer_sites"],
        "expected_answer": "Wallet drainer phishing sites impersonate legitimate DeFi protocols, airdrop sites, or NFT mints. They request unlimited token approvals and then use transferFrom to drain victim wallets. They typically use urgent language to pressure users.",
        "collection": "scam_intel",
    },
    # -- news_articles --
    {
        "query": "What recent SEC actions target crypto scams?",
        "relevant_doc_ids": ["sec_crypto_actions", "regulatory_scam_news"],
        "expected_answer": "Recent SEC actions targeting crypto scams include enforcement against unregistered securities, charges against fraudulent ICOs, actions against DeFi protocols misleading investors, and coordination with international agencies on cross-border fraud.",
        "collection": "news",
    },
    {
        "query": "How are law enforcement agencies tracking crypto criminals?",
        "relevant_doc_ids": ["law_enforcement_crypto", "crypto_crime_tracking"],
        "expected_answer": "Law enforcement uses blockchain analysis tools, exchange KYC data, international cooperation through agencies like Europol, and subpoena power to track crypto criminals. Chain analysis firms like Chainalysis and Elliptic provide investigation support.",
        "collection": "news",
    },
    # -- transaction_patterns --
    {
        "query": "What transaction patterns indicate a flash loan attack?",
        "relevant_doc_ids": ["flash_loan_attack_pattern", "tx_flash_loan_indicators"],
        "expected_answer": "Flash loan attack transaction patterns include: atomic transactions (borrow, manipulate, profit, repay in one transaction), price oracle manipulation, and large borrowing relative to protocol TVL. Look for single-block transactions with massive value flow.",
        "collection": "threat_intel",
    },
    {
        "query": "How to identify sandwich attack transactions in mempool?",
        "relevant_doc_ids": ["sandwich_tx_pattern", "mempool_sandwich_detection"],
        "expected_answer": "Sandwich attacks appear as three related transactions: front-run (buy before victim), victim transaction, and back-run (sell after victim). Look for same-sender transactions bracketing a target transaction with higher gas prices.",
        "collection": "threat_intel",
    },
    {
        "query": "What on-chain patterns reveal a rug pull in progress?",
        "relevant_doc_ids": ["rug_pull_tx_pattern", "on_chain_rug_signals"],
        "expected_answer": "On-chain rug pull patterns include: liquidity removal transactions from the deployer, large token mints before price collapse, ownership transfer to a burn address right before the drain, and coordinated selling across multiple funded wallets.",
        "collection": "threat_intel",
    },
    # -- More wallet_profiles --
    {
        "query": "What is a Sybil attack in crypto and how to detect it?",
        "relevant_doc_ids": ["sybil_detection_wallet", "multi_wallet_pattern"],
        "expected_answer": "Sybil attacks use multiple fake identities to manipulate voting or airdrops. Detection involves finding wallets funded by the same source, similar transaction patterns across wallets, and coordinated behavior in governance proposals.",
        "collection": "scam_intel",
    },
    {
        "query": "How to trace funds from a compromised wallet?",
        "relevant_doc_ids": ["fund_tracing_wallet", "compromised_wallet_forensic"],
        "expected_answer": "Fund tracing from compromised wallets involves following transaction flows through chain hops, identifying exchange deposits for KYC retrieval, tracking mixer interactions, and monitoring for consolidations into new wallets.",
        "collection": "scam_intel",
    },
    # -- More token_analysis --
    {
        "query": "What tokenomics red flags indicate a potential scam?",
        "relevant_doc_ids": ["tokenomics_red_flags", "scam_tokenomics_analysis"],
        "expected_answer": "Tokenomics red flags include: >50% supply held by top 10 wallets, team allocation unlockable immediately, no vesting schedule, high tax rates, and large marketing allocation with no development budget.",
        "collection": "scam_intel",
    },
    {
        "query": "Is $SOL a rug pull risk?",
        "relevant_doc_ids": ["sol_token_analysis", "solana_rug_indicators"],
        "expected_answer": "SOL itself is not a rug pull risk as it is a major chain token. However, tokens on the Solana chain frequently exhibit rug pull patterns due to low barriers to token creation. Always verify specific token contracts.",
        "collection": "scam_intel",
    },
    {
        "query": "What does a honeypot detection scan reveal?",
        "relevant_doc_ids": ["honeypot_scan_results", "detection_scan_honeypot"],
        "expected_answer": "A honeypot detection scan reveals: buy possible but sell impossible, extreme sell tax, trading time locks, wallet blacklisting capability, and max transaction limits that prevent meaningful sells.",
        "collection": "scam_intel",
    },
    # -- More scam_patterns --
    {
        "query": "How do liquidity drain backdoors work in DeFi protocols?",
        "relevant_doc_ids": ["liquidity_drain_backdoor", "defi_drain_mechanism"],
        "expected_answer": "Liquidity drain backdoors use hidden functions like manualSwap or rescueToken that allow the owner to call router.removeLiquidity and extract all pool funds. They are often obscured within complex swap logic.",
        "collection": "scam_intel",
    },
    {
        "query": "What is the difference between a rug pull and a honeypot?",
        "relevant_doc_ids": ["rug_vs_honeypot", "scam_type_comparison"],
        "expected_answer": "A rug pull involves the creator actively draining liquidity, while a honeypot prevents victims from selling through contract code restrictions. Rug pulls result in immediate total loss; honeypots trap value over time.",
        "collection": "scam_intel",
    },
    # -- More forensic_reports --
    {
        "query": "How was the Ronin Bridge hack investigated?",
        "relevant_doc_ids": ["ronin_bridge_forensic", "ronin_hack_investigation"],
        "expected_answer": "The Ronin Bridge hack was investigated by tracing the stolen ETH through mixer transactions, identifying compromised validator keys, and correlating the attack with social engineering of Axie DAO validator nodes.",
        "collection": "hacks",
    },
    # -- More known_scams --
    {
        "query": "What are common DeFi protocol exploit patterns from 2024?",
        "relevant_doc_ids": ["defi_exploits_2024", "known_protocol_hacks"],
        "expected_answer": "Common 2024 DeFi exploit patterns include: oracle manipulation attacks, flash loan governance takeovers, reentrancy in new pool implementations, and access control bypass via compromised admin keys.",
        "collection": "scam_intel",
    },
    {
        "query": "What are known crypto mixing services used by criminals?",
        "relevant_doc_ids": ["known_mixer_services", "mixing_service_criminal"],
        "expected_answer": "Known crypto mixing services used in criminal activity include Tornado Cash, Blender.io, and Sinbad. These services break the on-chain link between sender and receiver, making fund tracing difficult.",
        "collection": "scam_intel",
    },
    # -- More market_intel --
    {
        "query": "How does whale wallet movement predict market crashes?",
        "relevant_doc_ids": ["whale_crash_prediction", "market_whale_signals"],
        "expected_answer": "Whale wallet movements that predict crashes include: large transfers to exchanges, sudden unlocking of staked tokens, coordinated selling across multiple whale wallets, and liquidity withdrawal from DeFi protocols.",
        "collection": "news",
    },
    # -- More contract_audits --
    {
        "query": "What audit findings indicate high risk in upgradeable contracts?",
        "relevant_doc_ids": ["upgradeable_audit_risks", "proxy_audit_findings"],
        "expected_answer": "High-risk audit findings in upgradeable contracts include: no timelock on upgrades, single-admin upgrade control, missing implementation validation, and proxy admin that can be changed without governance approval.",
        "collection": "threat_intel",
    },
    {
        "query": "How do gas limit patterns reveal malicious contract behavior?",
        "relevant_doc_ids": ["gas_limit_audit", "malicious_gas_patterns"],
        "expected_answer": "Malicious contracts use unusual gas patterns such as: infinite loops in transfer functions that consume all gas, hidden state changes in fallback functions, and gas-griefing attacks that make certain operations prohibitively expensive.",
        "collection": "threat_intel",
    },
    # -- More transaction_patterns --
    {
        "query": "What is a dusting attack and how to detect it?",
        "relevant_doc_ids": ["dusting_attack_tx", "dust_transaction_pattern"],
        "expected_answer": "A dusting attack sends tiny amounts of crypto to many wallets to track their activity. Detection involves identifying micro-transactions below normal thresholds (dust), from unknown senders, targeting multiple wallets simultaneously.",
        "collection": "threat_intel",
    },
    {
        "query": "How do governance attack transactions work on-chain?",
        "relevant_doc_ids": ["governance_attack_tx", "dao_vote_manipulation"],
        "expected_answer": "Governance attack transactions involve flash-loan-borrowed tokens used to vote on proposals, then repaid in the same transaction. On-chain detection shows: borrowed governance tokens, proposal votes, and token returns all in one block.",
        "collection": "threat_intel",
    },
    # -- final additional to hit 50+ --
    {
        "query": "What indicators show a token is using fake volume on CoinGecko?",
        "relevant_doc_ids": ["fake_volume_indicators", "coingecko_wash_signals"],
        "expected_answer": "Indicators of fake volume on CoinGecko include: volume/liquidity ratio exceeding 10x, repetitive identical-size transactions, price stability despite high volume, and wash trading detected by exchange pairing analysis.",
        "collection": "news",
    },
    {
        "query": "How are cross-chain bridge exploits prevented?",
        "relevant_doc_ids": ["bridge_security_prevention", "cross_chain_audit"],
        "expected_answer": "Bridge exploit prevention includes: multi-signature validation, timelocks on large transfers, regular security audits, monitoring of validator key compromises, and circuit breakers that pause operations on anomaly detection.",
        "collection": "threat_intel",
    },
]


# ======================================================================
# METRICS
# ======================================================================


def context_precision(
    retrieved_ids: list[str], relevant_ids: list[str], k: int | None = None
) -> float:
    """
    Compute nDCG@k (Normalized Discounted Cumulative Gain).
    Measures ranking quality of retrieved documents.
    """
    if not relevant_ids or not retrieved_ids:
        return 0.0

    k = k or len(retrieved_ids)
    k = min(k, len(retrieved_ids))
    if k == 0:
        return 0.0

    # DCG
    dcg = 0.0
    for i in range(k):
        doc_id = retrieved_ids[i] if i < len(retrieved_ids) else ""
        rel = 1.0 if doc_id in relevant_ids else 0.0
        dcg += rel / math.log2(i + 2)  # i+2 because positions are 1-indexed

    # Ideal DCG
    ideal_rels = [1.0] * min(len(relevant_ids), k)
    ideal_rels += [0.0] * (k - len(ideal_rels))
    idcg = 0.0
    for i, rel in enumerate(ideal_rels):
        idcg += rel / math.log2(i + 2)

    if idcg == 0:
        return 0.0

    return dcg / idcg


def context_recall(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """
    Percentage of relevant documents found in retrieved results.
    """
    if not relevant_ids:
        return 1.0  # nothing to recall

    found = sum(1 for rid in relevant_ids if rid in set(retrieved_ids))
    return found / len(relevant_ids)


def hit_rate(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """
    Binary: 1.0 if at least one relevant doc was found, 0.0 otherwise.
    """
    if not relevant_ids:
        return 1.0
    return 1.0 if any(rid in set(retrieved_ids) for rid in relevant_ids) else 0.0


def mrr(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """
    Mean Reciprocal Rank: 1/rank of first relevant document.
    """
    if not relevant_ids:
        return 1.0
    for i, doc_id in enumerate(retrieved_ids):
        if doc_id in set(relevant_ids):
            return 1.0 / (i + 1)
    return 0.0


async def faithfulness(answer: str, context_chunks: list[str]) -> float:
    """
    Check if answer claims are supported by context.
    Uses NLI from hallucination_guard if available, otherwise keyword overlap.
    """
    if not answer or not context_chunks:
        return 0.0

    try:
        from app.hallucination_guard import (  # type: ignore[import-not-found]
            HallucinationGuard,
        )  # external dependency  # pyright: ignore[reportMissingImports]

        guard = await HallucinationGuard.get_guard()
        combined_context = "\n\n".join(context_chunks[:5])
        result = await guard.check_answer(answer, combined_context)
        return float(result.confidence if result.is_faithful else 1.0 - result.confidence)
    except Exception as e:
        logger.debug("NLI faithfulness failed, using keyword overlap: %s", e)
        return _keyword_faithfulness(answer, context_chunks)


def _keyword_faithfulness(answer: str, context_chunks: list[str]) -> float:
    """
    Simple keyword overlap faithfulness metric.
    Fraction of answer keywords found in context.
    """
    # Extract meaningful keywords from answer (skip stop words)
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "if",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "we",
        "you",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "their",
        "our",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "than",
        "too",
        "very",
    }

    answer_words = {
        w.lower() for w in re.findall(r"\b\w{3,}\b", answer) if w.lower() not in stop_words
    }

    if not answer_words:
        return 0.5  # can't determine

    combined_context = " ".join(context_chunks).lower()
    supported = sum(1 for w in answer_words if w in combined_context)

    return supported / len(answer_words)


async def answer_relevancy(answer: str, query: str) -> float:
    """
    Cosine similarity between answer embedding and query embedding.
    Uses the local BGE embedder.
    """
    if not answer or not query:
        return 0.0

    try:
        from rugbrain.crypto_embeddings import get_embedder

        embedder = await get_embedder()

        answer_vec = await embedder._semantic_embed_one(answer, "semantic")
        query_vec = await embedder._semantic_embed_one(query, "semantic")

        # Cosine similarity
        a = np.array(answer_vec)
        q = np.array(query_vec)
        sim = float(np.dot(a, q) / (np.linalg.norm(a) * np.linalg.norm(q) + 1e-8))

        # Normalize to [0, 1] (cosine sim is [-1, 1])
        return max(0.0, (sim + 1.0) / 2.0)
    except Exception as e:
        logger.debug("Answer relevancy embedding failed: %s", e)
        return _keyword_relevancy(answer, query)


def _keyword_relevancy(answer: str, query: str) -> float:
    """Fallback keyword-based relevancy."""
    query_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", query)}
    answer_words = {w.lower() for w in re.findall(r"\b\w{3,}\b", answer)}
    if not query_words:
        return 0.0
    overlap = len(query_words & answer_words)
    return overlap / len(query_words)


# ======================================================================
# EVALUATION DATA MODELS
# ======================================================================


@dataclass
class QueryMetrics:
    query: str
    collection: str
    retrieved_ids: list[str] = field(default_factory=list)
    relevant_ids: list[str] = field(default_factory=list)
    ndcg: float = 0.0
    recall: float = 0.0
    mrr_value: float = 0.0
    hit: float = 0.0
    faithfulness_score: float = 0.0
    answer_relevancy_score: float = 0.0
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class EvaluationReport:
    total_queries: int = 0
    per_query_metrics: list[QueryMetrics] = field(default_factory=list)
    mean_ndcg: float = 0.0
    mean_recall: float = 0.0
    mean_mrr: float = 0.0
    hit_rate_at_k: float = 0.0
    mean_faithfulness: float = 0.0
    mean_answer_relevancy: float = 0.0
    mean_latency_ms: float = 0.0
    median_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    errors: int = 0
    collection: str | None = None


# ======================================================================
# EVALUATION RUNNER
# ======================================================================


async def run_evaluation(
    collection: str | None = None,
    limit: int = 10,
) -> EvaluationReport:
    """
    Run RAG evaluation against the golden test set.

    For each golden query:
      1. Call search_similar to retrieve documents (bounded by ``EVAL_TIMEOUT_S``
         via ``asyncio.wait_for`` — a hung search times out instead of stalling
         the run; queries execute concurrently under ``EVAL_MAX_CONCURRENCY``)
      2. Compute metrics against golden relevant_doc_ids
      3. Compute faithfulness and answer_relevancy against expected_answer

    Returns EvaluationReport with per-query and aggregate metrics.
    """
    from rugbrain.search_service import search_similar

    # Filter test set by collection if specified
    test_cases = GOLDEN_TEST_SET
    if collection:
        test_cases = [t for t in test_cases if t.get("collection") == collection]

    if not test_cases:
        return EvaluationReport(
            total_queries=0,
            collection=collection,
        )

    # Bound the concurrency so embedding/search backends are not hammered.
    semaphore = asyncio.Semaphore(EVAL_MAX_CONCURRENCY)

    async def _run_one(tc: dict[str, Any]) -> QueryMetrics:
        q = tc["query"]
        rel_ids = tc.get("relevant_doc_ids", [])
        expected = tc.get("expected_answer", "")
        coll = tc.get("collection", "scam_intel")

        start = time.monotonic()
        try:
            results = await asyncio.wait_for(
                search_similar(q, collection=coll, limit=limit, min_similarity=0.3),
                timeout=EVAL_TIMEOUT_S,
            )
            latency = (time.monotonic() - start) * 1000

            retrieved_ids = [r.get("id", "") for r in results]
            context_chunks = [r.get("content", "") for r in results]

            # Compute retrieval metrics
            ndcg_val = context_precision(retrieved_ids, rel_ids, k=limit)
            recall_val = context_recall(retrieved_ids, rel_ids)
            mrr_val = mrr(retrieved_ids, rel_ids)
            hit_val = hit_rate(retrieved_ids, rel_ids)

            # Compute generation metrics (if we have expected answer)
            faith_val = 0.0
            rel_val = 0.0
            if expected and context_chunks:
                faith_val = await asyncio.wait_for(
                    faithfulness(expected, context_chunks), timeout=EVAL_TIMEOUT_S
                )
            if expected:
                rel_val = await asyncio.wait_for(
                    answer_relevancy(expected, q), timeout=EVAL_TIMEOUT_S
                )

            return QueryMetrics(
                query=q,
                collection=coll,
                retrieved_ids=retrieved_ids,
                relevant_ids=rel_ids,
                ndcg=round(ndcg_val, 4),
                recall=round(recall_val, 4),
                mrr_value=round(mrr_val, 4),
                hit=round(hit_val, 4),
                faithfulness_score=round(faith_val, 4),
                answer_relevancy_score=round(rel_val, 4),
                latency_ms=round(latency, 2),
            )

        except TimeoutError:
            latency = (time.monotonic() - start) * 1000
            return QueryMetrics(
                query=q,
                collection=coll,
                relevant_ids=rel_ids,
                error=f"timeout after {EVAL_TIMEOUT_S}s",
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return QueryMetrics(
                query=q,
                collection=coll,
                relevant_ids=rel_ids,
                error=str(e),
                latency_ms=round(latency, 2),
            )

    async def _run_with_semaphore(tc: dict[str, Any]) -> QueryMetrics:
        async with semaphore:
            return await _run_one(tc)

    per_query = await asyncio.gather(*(_run_with_semaphore(tc) for tc in test_cases))
    errors = sum(1 for p in per_query if p.error)

    # Aggregate metrics
    valid = [p for p in per_query if not p.error]
    n = len(valid) or 1

    report = EvaluationReport(
        total_queries=len(test_cases),
        per_query_metrics=per_query,
        mean_ndcg=round(sum(p.ndcg for p in valid) / n, 4),
        mean_recall=round(sum(p.recall for p in valid) / n, 4),
        mean_mrr=round(sum(p.mrr_value for p in valid) / n, 4),
        hit_rate_at_k=round(sum(p.hit for p in valid) / n, 4),
        mean_faithfulness=round(sum(p.faithfulness_score for p in valid) / n, 4),
        mean_answer_relevancy=round(sum(p.answer_relevancy_score for p in valid) / n, 4),
        mean_latency_ms=round(sum(p.latency_ms for p in valid) / n, 2) if valid else 0,
        errors=errors,
        collection=collection,
    )

    # Latency stats
    if valid:
        latencies = sorted(p.latency_ms for p in valid)
        report.median_latency_ms = round(latencies[len(latencies) // 2], 2)
        p95_idx = int(len(latencies) * 0.95)
        report.p95_latency_ms = round(latencies[min(p95_idx, len(latencies) - 1)], 2)

    return report
