"""Chunk 6 of ``EXPERT_DOCS`` (generated; literal data only).

Source: scripts/crypto_expert_knowledge_data.py
"""

ITEMS = [
    {
            "source": "crypto-expert",
            "rel_key": "mev/defensive-design",
            "title": "MEV-resistant protocol design patterns",
            "topic": "mev",
            "knowledge_type": "expert_guide",
            "collection": "contract_security",
            "content": (
                "Protocols can design against MEV rather than fight it "
                "reactively. Patterns: 1) commit-reveal — hide intent until "
                "execution (randomness and sealed bids), 2) batch/auction "
                "settlement — process trades in batches at a uniform price "
                "(periodic batch auctions), 3) time-weighted pricing — TWAP "
                "oracles over spot to defeat instantaneous manipulation, "
                "4) fair ordering — commit-based sequencing that punishes "
                "reordering, 5) internalize MEV — run your own auction "
                "(PBS-style) and distribute profits to LPs/users, 6) "
                "minimum-delay buffers, 7) variable rate-limiting on "
                "governance-sensitive actions. Audit checklist: enumerate "
                "every state change that depends on transaction order or "
                "spot prices; ask 'can a bot profitably reorder, insert, or "
                "censor this?'; assume adversarial ordering in threat "
                "models. MEV resistance is a property you design in, not a "
                "patch you add later."
            ),
        },
    {
            "source": "crypto-expert",
            "rel_key": "security/cei-pattern",
            "title": "Checks-Effects-Interactions — the core defensive pattern",
            "topic": "smart-contract-security",
            "knowledge_type": "expert_guide",
            "collection": "contract_security",
            "content": (
                "Checks-Effects-Interactions (CEI) is the foundational pattern "
                "against reentrancy. Order every function: 1) Checks — validate "
                "conditions (require/auth, balances, prices, deadlines), 2) "
                "Effects — update state first (balances, flags, mappings), 3) "
                "Interactions — only then make external calls (transfers, "
                "CPIs, callbacks). Because state is already updated before the "
                "external call, a reentrant call sees the new state and cannot "
                "double-withdraw. Example: in withdraw(), subtract the balance "
                "from storage BEFORE the transfer — a malicious recipient "
                "re-entering the function finds a zeroed balance. CEI also "
                "protects against cross-function reentrancy. Add explicit "
                "reentrancy guards (ReentrancyGuard/nonReentrant) for defense "
                "in depth, and beware read-only reentrancy where view "
                "functions report stale state to oracles/aggregators. On "
                "Solana, apply the same discipline to account mutations before "
                "CPIs."
            ),
        },
    {
            "source": "crypto-expert",
            "rel_key": "security/access-control",
            "title": "Access control patterns — roles, owners, and upgrades",
            "topic": "smart-contract-security",
            "knowledge_type": "expert_guide",
            "collection": "contract_security",
            "content": (
                "Access control limits who can call privileged functions. "
                "Patterns: Ownable (single owner), Roles (OpenZeppelin "
                "AccessControl — granular roles like MINTER_ROLE, PAUSER_ROLE, "
                "each with an admin), timelock (privileged actions execute "
                "only after a delay — users can exit before changes land), and "
                "multisig (M-of-N signers for admin keys). Rules: never use "
                "tx.origin for authorization (phishing vector); always use "
                "msg.sender / explicit signer accounts; grant the minimum "
                "permission needed; make admin changes two-step (set + "
                "confirm) so a typo doesn't brick the contract; document every "
                "privileged role in the threat model. Upgradeable contracts "
                "add proxy risk — the admin can change implementation, so the "
                "upgrade key IS the contract's security. Audit findings "
                "repeatedly show: missing onlyOwner, exposed internal "
                "functions, and single-step dangerous setters."
            ),
        },
    {
            "source": "crypto-expert",
            "rel_key": "security/oracle-manipulation",
            "title": "Oracle manipulation — why spot prices lie",
            "topic": "smart-contract-security",
            "knowledge_type": "expert_guide",
            "collection": "contract_security",
            "content": (
                "Oracle manipulation attacks feed a protocol a false price to "
                "profit from the difference. The classic vector: reading spot "
                "price from an AMM pool the attacker can manipulate — flash-"
                "loan a huge amount into the pool, move the spot price, trigger "
                "the victim protocol's dependent action (liquidation, mint, "
                "swap), then unwind the flash loan. Defenses: 1) TWAP — "
                "time-weighted average price over N blocks smooths out "
                "manipulation (Uniswap v2/v3 TWAP oracles), 2) Chainlink-style "
                "aggregated oracles from multiple independent sources with "
                "deviation thresholds, 3) circuit breakers — pause the "
                "dependent action when price moves beyond a band, 4) "
                "validation — require multiple oracles agree, 5) avoid "
                "oracle-read in the same transaction as the pool trade. "
                "Audit checklist: every price consumer must ask 'what if the "
                "price is wrong for one block?' and survive it. Depegged "
                "assets and illiquid pools are the highest-risk inputs."
            ),
        },
    {
            "source": "crypto-expert",
            "rel_key": "security/flash-loan-defenses",
            "title": "Flash loan attack defenses",
            "topic": "smart-contract-security",
            "knowledge_type": "expert_guide",
            "collection": "contract_security",
            "content": (
                "Flash loans let anyone borrow unlimited capital for one "
                "transaction, making capital-intensive attacks free to "
                "execute — oracle manipulation, governance takeovers, and "
                "arbitrage. You cannot stop flash loans, but you can "
                "neutralize the vulnerabilities they amplify: 1) never trust "
                "spot prices — use TWAP or external oracles, 2) time-lock "
                "governance changes so a flash-loan voter cannot swing an "
                "election (check voting power snapshot at block N, not live), "
                "3) bound rate changes and collateral factors with deviation "
                "limits, 4) validate invariants — e.g. protocol solvency after "
                "every action, 5) design functions to be manipulation-"
                "insensitive (the outcome must not depend on instantaneous "
                "pool state). Test with real flash-loan PoCs: simulate the "
                "largest possible borrow against every price-dependent "
                "function. A function that survives an unlimited flash loan is "
                "a function that survives most exploits."
            ),
        },
    {
            "source": "crypto-expert",
            "rel_key": "security/proxy-upgrade-safety",
            "title": "Proxy upgrade safety — storage layout and initializers",
            "topic": "smart-contract-security",
            "knowledge_type": "expert_guide",
            "collection": "contract_security",
            "content": (
                "Upgradeable proxies (UUPS, transparent, beacon) keep the "
                "contract address stable while the implementation changes — "
                "but introduce storage-layout hazards. Critical rules: 1) "
                "never reorder/remove existing state variables in the "
                "implementation (storage collision corrupts data), 2) append "
                "new variables at the end, 3) use storage gap patterns in "
                "inherited contracts to reserve slots, 4) initializers must be "
                "called once and protected (a public initialize() callable by "
                "anyone bricks the proxy), 5) verify the implementation "
                "contract's constructor doesn't write state (it runs in "
                "isolation, not in proxy context), 6) keep the upgrade "
                "function access-controlled and timelocked, 7) validate the "
                "new implementation address (not a selfdestructed contract, "
                "not a malicious one). Audit checklist: storage layout diff "
                "between versions, initializer guards, and upgrade "
                "authorization. Mango, Wormhole, and multiple bridge hacks "
                "trace to proxy or initializer flaws."
            ),
        },
]
