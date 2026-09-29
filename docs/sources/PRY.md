# Pry self-hosted scraping

```python
Self-hosted Pry scraper client.

Pry (https://api.pryscraper.com) is a self-hostable scraping API with a 15-tier
anti-bot bypass, an OCR and a PDF extractor, and 112 MCP tools. It runs on this
host as the container ``pry`` on 127.0.0.1:8005.

WHY THIS MODULE EXISTS. Every prior caller of Pry on this fleet concluded it
was broken. It was not. Pry is x402 pay-per-call by default, and an
unauthenticated call to ``POST /v1/scrape`` returns::

    payment required: send 1000 USDC to 0x1E3A... on eip155:8453

That 402 reads exactly like a dead service. Three other things make it harder
to read correctly, and each cost a real debugging session:

  * Pry runs its PAID compose file here, not ``docker-compose.selfhost.yml``.
  * The first-party "internal" path (``x-internal-token``) needs THREE headers,
    not one: a token, an HMAC-SHA256 over ``f"{METHOD}\\n{path}\\n{caller}\\n{ts}"``,
    and a timestamp inside a 300s skew window. The ``caller`` is not chosen by
    the caller, it is derived from the ASGI socket peer, so it is awkward to
    drive from curl.
  * The response nests the text at ``data.markdown``. Reading ``content`` or a
    top-level ``markdown`` returns None, which looks like an empty scrape.

So this module exists to make all three impossible to get wrong: one function,
one documented key, one response shape, one exception type that says which
layer failed.

AUTH — WHY THE OWNER KEY AND NOT ``PRY_X402_ENABLED=false``. Pry has two
supported free paths and they are NOT equivalent:

  1. The OWNER KEY (this module). ``Authorization: Bearer $PRY_API_KEY`` is
     checked by ``payments/x402_middleware/owner.py`` and short-circuits the x402
     gate in ``enforcement.py`` before payment is ever considered. Zero config
     change, works today, proven live. This is the ONLY path used here.
  2. ``PRY_X402_ENABLED=false`` on the container. This disables charging for
     EVERY caller including external ones, so it is correct only if this host
     is never reachable from the internet. The operator chose the owner key on
     2026-08-29 precisely to avoid that exposure.

Do not "simplify" this by setting PRY_X402_ENABLED=false on an internet-facing
host. That turns a paywalled API into an open one.

ENV
  PRY_URL         base URL, default http://127.0.0.1:8005
  PRY_API_KEY     owner key. If unset, resolved once from the running container
                  (`docker inspect pry`), because the key lives in the
                  container env and not in any repo-tracked file.

```
