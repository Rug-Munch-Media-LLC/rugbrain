# RugBrain Product Blueprint — 40 Best-in-Class Features

> 2026-08-25 · Strategy doc · Grounded in shipped infra: 70-collection Qdrant
> taxonomy, dual-tier local AI (4B deep / 0.6B speed), standalone firehose,
> TG bot distribution, x402 payment rails.

## A. Total Market Awareness (the firehose foundation)

1. **Whole-Market Pulse Page** — one query returns mcap, dominance, FNG,
   funding extremes, stablecoin flows, OI leaders (all collections now fed).
2. **Whale Replay** — reconstruct any token's whale accumulation history from
   whale_transfers + wallet_v2 ("who built this position, when, at what price").
3. **Unlock Countdown Radar** — proactive alerts N days before vesting cliffs;
   cross-ref holder concentration to score dump risk.
4. **Liquidation Cascade Forecaster** — funding + OI + price distance to
   liquidation clusters → "cascade risk" score per major asset.
5. **Stablecoin Flow Sentinel** — mint/redeem spikes as the smart money
   signal; alert on USDT supply jumps >Xσ.
6. **Regulatory Event Impact Scorer** — SEC/DOJ items scored against exposed
   tokens (portfolio-aware: "this suit touches 3 tokens you hold").
7. **Exploit Pattern Early Warning** — DefiLlama hacks + our own scam_patterns:
   new exploit technique detected anywhere → pattern-match all holdings.
8. **Narrative Tracker** — embedding drift over news_v2 shows rising narratives
   before price ("RWA tokenization mentions +340% week-over-week").

## B. Predictive & Leading Indicators

9. **Smart Money Echo** — when a wallet with proven alpha (wallet_v2 track
   record) buys, notify followers of that wallet's public watchlist.
10. **Deployer Reputation Graph** — every deployer ever seen, scored; new
    token from a serial rugging deployer flagged at block zero.
11. **Liquidity Death Clock v2** — survival prediction trained on LP-pull
    patterns already in threat_intel_v2.
12. **Bundle Detection as a Service** — our bundle/cluster engine offered via
    API so other tools can check any swap pre-trade.
13. **Funding Squeeze Map** — hlwhales data → visualize which side is
    overcrowded; squeeze direction probability.
14. **Volatility Regime Classifier** — embed recent market_health snapshots;
    classify regime (trend/chop/capitulation) and switch bot verbosity.
15. **Token Age Risk Curve** — statistical death curve by age band + liquidity
    tier; "tokens like this die within X days with Y% probability".
16. **Cross-Chain Contagion Alerts** — bridge exploit on chain A → instant
    exposure map of bridged assets on chains B..N.

## C. Deep Forensics & Attribution

17. **Ask-the-Chain** — natural language over wallet_v2: "who funded the
    top 10 holders of $X" answered from embedded forensics, not LLM guesses.
18. **Wash-Trade Volume Fingerprint** — exchangeaudit engine extended to any
    DEX pair; volume authenticity score on every token page.
19. **Insider Allocation Detector** — cluster sniping analysis at launch:
    % supply grabbed by insider clusters in first N blocks.
20. **Honeypot Simulation Sandbox** — simulate sell on a forked state before
    you buy; show exact revert reason.
21. **Contract Diff Alerts** — watch upgradeable proxies; diff the
    implementation the moment it changes, GPT-summarized impact.
22. **Phishing Domain Watch** — typosquat detection on brand domains +
    malicious-approval contract clustering, fed into social_intel.
23. **Mev Exposure Report** — per-wallet historical MEV paid/sandwiched;
    "you lost $340 to sandwiches this month, here's the route fix".
24. **Evidence Bundles** — one-click exportable forensic report (PDF/JSON)
    chain-of-custody style for law-enforcement or bounty submission.

## D. Personalization & Memory (RMI agent_memory advantage)

25. **Per-User Risk Profile Memory** — RAG remembers what you hold, your risk
    appetite, past questions; answers arrive pre-filtered to your context.
26. **Watchlist Autopsy** — daily digest: what changed overnight on YOUR
    watchlist (LP moves, holder churn, unlock proximity, sentiment shift).
27. **Conversational Portfolio Stress-Test** — "what happens to my portfolio
    if ETH drops 30%" answered with real positions from memory.
28. **Learning Alerts** — alerts that adapt: mute what you ignore, amplify
    what you act on (engagement feedback loop into importance scoring).
29. **Strategy Journal** — auto-log every scan/alert/action into personal
    RAG memory; "why did I buy this?" retrievable forever.
30. **Team Spaces** — shared memory pools for trading groups; shared
    watchlists, shared forensics, group-level alert routing.

## E. Distribution & Experience

31. **Proactive Daily Briefing** — 60-second morning audio/text brief:
    overnight exploits, whale moves, unlock calendar, narrative shifts.
32. **Inline Token Cards Anywhere** — paste any address in ANY chat (not just
    commands) → instant rich card; zero-friction scanning.
33. **Mini-App Dashboard** — Telegram mini-app: live portfolio, cascade map,
    whale replay visualizations from the bot's data plane.
34. **Webhook/API Parity** — every bot capability available via API key
    (x402-monetizable): bots paying bots.
35. **Multilingual Intel** — ingest CN/KR/RU/AR sources (PANews etc. already
    flowing) → translated, deduped, earlier than English CT.
36. **Voice Ask** — voice note in, spoken answer out (whisper-local + TTS);
    hands-free degen intel.

## F. Moat & Business

37. **Reputation-Staked Signals** — analysts publish signals staked with
    x402 micropayments; accurate callers earn; RAG indexes performance.
38. **Data Licensing Layer** — our cleaned whale/scam/unlock datasets served
    via metered API (the data bus becomes a product).
39. **Incident Retainer Mode** — protocols pre-register; when their address
    appears in an exploit trace, they get immediate forensics + comms pack.
40. **Public Safety Index** — publishable "RugBrain Safety Index" per token —
    the standard score other projects quote; the metric IS the moat.

## Sequencing note

Quick wins first: #32 (inline cards), #26 (watchlist autopsy), #4/#5 (already
have the adapters), #31 (briefing = aggregation of existing feeds). Each ships
value while building toward the moat features (#37–40).
