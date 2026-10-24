# Content style — the canonical contract

Owner: Rug Munch Media LLC research desk. Status: canonical.

**The machine-readable source is `config/content_style.json`.** This page
explains it; it does not add rules. `config/content_prompts.json` holds the
prompt templates and contains no style rules of its own — every prompt injects
the style block at render time, so the two files cannot drift. Code that
renders copy reads the JSON through `rugbrain.content_style` and gates it
through `rugbrain.content_style_lint`. There is no second copy of these rules
in any prompt string, doc or template.

## Voice

Direct, knowledgeable, non-hype. Third person, written by a research desk for
readers who already know the vocabulary. Every piece follows one arc:

1. state what happened,
2. explain why it matters,
3. name who is affected,
4. link the evidence.

Never: hype, price predictions, return forecasts, financial advice, an
allegation without attribution, a number without a source, or a claim softened
into "could potentially".

The linter rejects the phrases in `voice.banned_phrases` and the
price-prediction patterns in `voice.banned_patterns`. Those lists are data —
edit the JSON, not the prose.

## Tense

| Part | Tense |
|---|---|
| Headline | present for standing facts, past for the event |
| Body | past for what happened, present for how the mechanism works |
| Analysis | present |
| Metadata / JSON fields | no tense; ISO values only |

## Capitalization

- Headline and body: sentence case.
- Tickers: `$BTC`, `$ETH`, `$SOL` — dollar prefix, uppercase, every reference.
- Protocol and product names: as the project writes them on its own site.
- Severity labels: `RUG`, `SCAM`, `EXPLOIT`, `NEWS`, `MARKET`, `REGULATORY`
  (from `rugbrain.formatting.lexicon`).
- All-caps body words are rejected outside tickers, severity labels and the
  acronym allowlist.
- Addresses: lowercase hex, truncated `0x1234…abcd`. Never a bare 40-character
  string in prose.

## Emoji

Allowlist only: 🚨 ⚠️ 🔍 📊 🛡️ 🧵 📌. Maximum one per post, on the headline
line, as a severity marker. No emoji in body paragraphs. Custom animated emoji
are opt-in via `rugbrain.formatting.emoji.set_emoji_ids` and are never
hardcoded. Anything outside the allowlist fails the style gate.

## Spacing

- Exactly one blank line between paragraphs; never two.
- Single spaces inside a line. No trailing whitespace, no tabs.
- Lists use `- `; headings use `## `.
- Em dashes carry one space on each side.
- Straight quotes, not typographic quotes.

`normalise_text()` repairs these mechanically before linting; a human fixes
anything the linter still reports.

## Numbers and prices

| Kind | Format |
|---|---|
| Crypto amount | figures with unit: `5 BTC`, `100 SOL` |
| Percent | figure plus `%`, no space: `5%` — never "5 percent" |
| USD | `$1,250.00` — comma thousands, cents when they matter |
| Large USD | `$1.2M`, `$3.4B` above 1,000,000 |
| Token price | four significant figures: `$0.004821` |
| Date | `October 24, 2026` — no ordinals |
| Time | 24-hour with UTC: `14:05 UTC` |
| Metadata | ISO-8601: `2026-10-24T14:05:00Z` |

Approximate qualifiers ("about", "around", "roughly") in front of a verifiable
figure are rejected: a figure is exact or it is not published.

## Sources and citations

- Inline marker `[n]` immediately after the claim it supports.
- Sources section entry: `[n] Title — Publisher, October 24, 2026. URL`.
- Minimum two independent sources, two distinct publisher domains, each with a
  URL and a retrieval date.
- Priority: primary (block explorer, DEX pool data, verified contract source,
  protocol docs) over secondary (exchange notice, regulator filing, audit)
  over tertiary (news article). A single social post is never the sole source.
- An uncorroborated claim is omitted. In a draft it may appear with `[REVIEW]`.

If the evidence does not meet the minimum, the correct output is
`{"status": "insufficient_evidence", "reason": "..."}` — not a shorter piece
with thinner sourcing.

## Disclaimers

Every long-form or alert surface ends with its configured disclaimer:

- Daily Rug: "Rug Munch Media is an on-chain research desk. This is analysis,
  not financial advice. Every figure links to its source."
- Alert: "Not financial advice. Verify before you sign."

## Length budgets

| Surface | Budget |
|---|---|
| `telegram_short` | 1,024 chars |
| `telegram_thread` | 3,900 chars |
| `feed_longform` | 12,000 chars |
| `x_post` | 280 chars |
| `alert` | 900 chars |
| `daily_rug` | 2,500–9,000 chars |
| Headline | 70 chars |
| Dek | 200 chars |
| Sections | 4–8 |
| Telegram hard limit | 4,096 chars (platform ceiling, never exceeded) |

## Prompt surfaces

`rugbrain.content_prompts.render(surface, topic=..., evidence=..., ...)` returns
the user message; `system_prompt(surface)` returns the system message with the
style contract injected. Surfaces: `telegram_short`, `telegram_thread`,
`feed_longform`, `x_post`, `alert`, `daily_rug`. Free models get a numbered
contract, an explicit output format and a character budget.

## Daily Rug

`rugbrain.daily_rug.generate_daily_rug(topic, date=..., search_fn=..., writer=...)`

1. Retrieves evidence from RugBrain RAG (`news_v2`, `research_v2`,
   `news_articles`, `social_intel`); hits without a URL are dropped.
2. Requires two citable sources from two publisher domains. Otherwise returns
   `insufficient_evidence` with the failing count.
3. Composes 4–8 sections, each carrying an `[n]` marker. The deterministic
   writer only attributes retrieved snippets, so it cannot invent a fact.
4. Optionally accepts a model draft, which is published only if every marker
   resolves and every number in it appears in the evidence. Otherwise the
   deterministic draft is used and the rejection is recorded under `writer`.
5. Emits `slug`, canonical `url` (`https://rugmunch.io/story/<slug>`), title,
   dek, sections, sources, token refs, chart refs, CTA, disclaimer, OpenGraph
   metadata, markdown body and a Telegram summary under 4,096 characters.

Statuses: `ok` (publishable), `insufficient_evidence` (nothing written),
`needs_review` (written, style gate objects — `style_violations` lists why).

## The gate

```python
from rugbrain.content_style_lint import lint_text, normalise_text

text = normalise_text(draft)
problems = lint_text(text, "telegram_short", source_count=len(sources))
```

Empty list means publishable. Non-empty means block: the gate never rewrites
meaning, and it never downgrades a rule to make a draft pass.
