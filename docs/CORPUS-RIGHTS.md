# CORPUS-RIGHTS — storage-right ladder

Status: canonical · owner: Rug Munch Media LLC Engineering · 2026-10-25

## The distinction that matters

We are a private crypto-intelligence platform. Ingested data is used
**internally, on our own server**; it is never resold or republished, and our
own terms do not forbid scraping. That is why internal use **widens what we may
keep** — but it does **not** erase a source's own licence, and it does **not**
override an explicit anti-scraping clause.

> **The binding constraint is the source's terms, not ours.**

This ladder therefore limits **what we persist**, never what we may read.
Reading is permitted; persistence is tiered. `pointer_only` still yields
useful intelligence — incident facts, dates, amounts, severity, entity and
chain attribution — we simply don't keep someone else's prose.

## The four tiers

| tier | when it applies | we store | we must not store |
|---|---|---|---|
| `full_text` | explicit permissive licence: MIT, Apache-2.0, CC-BY, CC-BY-SA, public domain | whole document, normalised, **plus a `licence` field** | nothing beyond the licence terms (attribution / share-alike / internal-only must be honoured) |
| `excerpt_cite` | no explicit licence, but publicly readable | **≤400-char excerpt**, title, url, date, class/severity, content hash — always cite + link | full-text mirror, bulk article bodies, comment threads, images |
| `pointer_only` | source terms forbid scraping/copying | metadata only: title, url, published_at, category, severity, `content_hash`, plus **facts we compute ourselves** (loss amount, chain, affected protocol, entity links) | any body text, quotes, excerpted prose, embedded images, verbatim excerpts |
| `reject` | credentials / paywall / login required, or automated access explicitly prohibited | nothing — **do not fetch** | everything (no request is issued) |

`full_text` with a copyleft or share-alike licence (GPL, CC-BY-SA) is stored
**internal-only with attribution** — we never republish the corpus, so
share-alike is satisfied by not distributing a derivative.

## Reclassification of previously rejected sources

A source rejected by an earlier audit as *"no licence"* is **not** automatically
`reject`. Reject is reserved for access control (paywall/login/credentials) or an
explicit prohibition on automated access.

| source | old verdict | new tier | rationale | we store | we must not store |
|---|---|---|---|---|---|
| **Rekt.news** | rejected: no licence | `pointer_only` | public post-mortems, no licence, mirroring refused | incident title/date/loss/chain/tags, slug, URL | article body, its prose, images |
| **Code4rena** | rejected: no licence | `excerpt_cite` | publicly readable, no explicit licence | ≤400-char finding excerpt, severity, contest, URL | full report dumps, bulk finding corpus |
| **Immunefi disclosures** | rejected: no licence | `pointer_only` | ToS restricts bulk copying | hack title/date/loss/project, URLs, derived facts | write-up body, quotes |
| **Solodit / Cyfrin** | rejected: no licence | `pointer_only` | aggregator ToS + upstream restrictions | finding title, severity, protocol, source URL | finding text, PDFs, mirrored reports |
| **Spearbit** | rejected: no licence | `pointer_only` | report portal restricts reproduction | report title/date/audit target, URL | report body, findings text |
| **Trail of Bits** | rejected: unclear | `full_text` (CC-BY-SA-4.0) | **internal-only + attribution** | full publications, licence field | external republication; attribution must be retained |
| **crytic/smartbugs** | rejected: no licence | `full_text` (Apache-2.0) | permissive, storage allowed | full dataset, licence field | anything Apache-2.0 does not permit (NOTICE retention) |

`PublicAuditFindingsSource` aggregates several of the above; it is declared
`excerpt_cite` at the source level and per-upstream caps apply inside it.
[REVIEW] Solodit/Cyfrin/Spearbit rows inside that source should be split into
their own `pointer_only` sub-sources rather than relying on an in-code comment.

## Enforcement in code

* `src/rugbrain/ingest/rights.py` — `StorageRight` enum, `DECLARATIONS`
  (source class name → tier), `require_right(source)`. Fails closed:
  `UndeclaredRight` for a missing declaration, `RightRefused` for `reject`.
* `src/rugbrain/ingest/rights_enforce.py` — `enforce_storage(doc, right)`:
  excerpt char budget (`EXCERPT_MAX_CHARS = 400`) and the `pointer_only`
  metadata allowlist. Raises `RightViolation` before anything is persisted.
* `tests/test_corpus_rights.py` — guard: every registered source declares a
  tier, no stale declarations, and tier allowances are enforced.

Declarations are keyed by **class name**, not `Source.name`, so an inconsistent
`name` attribute cannot let a source slip past the guard.

## Review cadence

Re-check on every new source (`docs/ADDING-A-SOURCE.md`) and whenever a source's
terms change. The ladder is a ratchet in one direction only: a tier may move
*up* on new evidence of a licence; moving *down* requires recording the licence
text or the ToS clause in this file.
