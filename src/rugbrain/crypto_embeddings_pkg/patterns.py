"""Known scam pattern templates (seed data)."""

from __future__ import annotations

from typing import Any

KNOWN_SCAM_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "Honeypot - Sell Disabled",
        "description": "Token where only the creator can sell. Buyers are trapped. Implemented via maxSellAmount=0, tradingEnabled=false, or blacklist of all non-owner addresses.",
        "indicators": [
            "maxSellAmount=0",
            "tradingEnabled=false",
            "onlyOwner can transfer",
            "blacklist all addresses",
        ],
        "severity": "critical",
        "code_snippets": [
            "maxSellAmount = 0",
            "tradingEnabled = false",
            "require(_isExcludedFromFees[msg.sender] || tradingEnabled)",
            "function enableTrading() external onlyOwner",
        ],
    },
    {
        "name": "Unlimited Mint / Rug Pull",
        "description": "Owner can mint unlimited tokens, diluting holders to zero before draining liquidity.",
        "indicators": [
            "unrestricted mint()",
            "onlyOwner mint",
            "no supply cap",
            "mint + removeLiquidity",
        ],
        "severity": "critical",
        "code_snippets": [
            "function mint(address to, uint256 amount) external onlyOwner",
            "mint(msg.sender, 1000000 * 10**decimals())",
        ],
    },
    {
        "name": "Hidden Fee Manipulation",
        "description": "Owner can set fees to 100% after launch, making sells impossible or stealing all transfers.",
        "indicators": [
            "setTax(100)",
            "dynamic fee > 50%",
            "setFee external onlyOwner",
            "no max fee cap",
        ],
        "severity": "high",
        "code_snippets": [
            "function setFee(uint256 _fee) external onlyOwner",
            "taxFee = 99",
            "liquidityFee = 99",
        ],
    },
    {
        "name": "Liquidity Drain Backdoor",
        "description": "Hidden function allowing owner to remove all liquidity. Often disguised as 'manualSwap' or 'rescueToken'.",
        "indicators": [
            "manualSwap",
            "removeLiquidity",
            "drain",
            "sweep",
            "rescueToken with onlyOwner",
        ],
        "severity": "critical",
        "code_snippets": [
            "function manualSwap() external onlyOwner",
            "function removeAllLiquidity() external onlyOwner",
            "uniswapV2Router.removeLiquidity",
        ],
    },
    {
        "name": "Proxy Upgrade Trap",
        "description": "Upgradeable proxy where admin can change implementation to a malicious contract post-launch.",
        "indicators": [
            "upgradeTo",
            "upgradeToAndCall",
            "UUPS",
            "TransparentUpgradeableProxy",
            "changeAdmin",
        ],
        "severity": "high",
        "code_snippets": [
            "function upgradeTo(address newImplementation) external onlyOwner",
            "function upgradeToAndCall(address newImplementation, bytes memory data) external onlyOwner",
        ],
    },
    {
        "name": "Bundle/Sniper Launch",
        "description": "Creator uses bundling to acquire large supply at launch before others can buy, then dumps.",
        "indicators": [
            "multi-wallet buy at block 0",
            "same funder",
            "bundle detection",
            "60%+ supply in 3 wallets",
        ],
        "severity": "high",
        "code_snippets": [],
    },
    {
        "name": "Honeypot - Max Tx Limit",
        "description": "Token with maxTxAmount set so low that only tiny amounts can be sold, trapping larger holders.",
        "indicators": [
            "maxTxAmount < 0.1% supply",
            "maxSellAmount micro",
            "anti-whale as antibot disguise",
        ],
        "severity": "high",
        "code_snippets": [
            "maxTxAmount = 1000",
            "maxSellAmount = 500",
            "maxWalletAmount = 2000",
        ],
    },
    {
        "name": "Fake Renounce / Ownership Hide",
        "description": "Claims ownership renounced but keeps control via secondary address, proxy, or timelock trick.",
        "indicators": [
            "fake renounce",
            "ownership not actually 0xdead",
            "hidden owner",
            "renounceOwnership not called",
        ],
        "severity": "high",
        "code_snippets": [],
    },
    {
        "name": "Sandwich Bot Tax Farm",
        "description": "Token with high taxes that benefits from MEV sandwich bots. Creator runs bots to extract from traders.",
        "indicators": ["tax > 10%", "sandwich pattern", "same wallet MEV + deployer"],
        "severity": "medium",
        "code_snippets": [],
    },
    {
        "name": "Wallet Drainer / Approval Scam",
        "description": "Contract tricks users into approving token spending, then drains their wallets via transferFrom.",
        "indicators": [
            "unlimited approval request",
            "transferFrom in unverified contract",
            "approveAndCall",
            "permit exploit",
        ],
        "severity": "critical",
        "code_snippets": [
            "IERC20(token).transferFrom(victim, attacker, balance)",
            "function claim(address token) external",
            "permit(",
        ],
    },
]
