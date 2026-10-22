-- 2026-10-22 · news_items classification + content-level dedupe
-- Owner: rugbrain RSS ingest (scripts/rss_ingest_feeds.py)
--
-- UP is additive and idempotent: three columns + two indexes, no rewrites, no
-- locks beyond brief ACCESS EXCLUSIVE on an empty-of-new-writes table operation.
-- DOWN drops the new indexes and columns; it is destructive for data written
-- after UP, so take a pg_dump first (recovery: re-run ingest with --days 30).

-- ── UP ────────────────────────────────────────────────────────────────
BEGIN;

ALTER TABLE news_items ADD COLUMN IF NOT EXISTS category     TEXT;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS source_trust REAL;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_news_category ON news_items(category);

-- UNIQUE on purpose: two feeds can surface the same story in the same second
-- (observed: Medium tag feeds). A check-then-insert races, a unique index does
-- not; the insert uses ON CONFLICT DO NOTHING. Legacy rows written before this
-- migration have content_hash NULL, so they do not participate.
-- Pre-flight (must return 0 duplicate groups before creating the index):
--   SELECT count(*) FROM (SELECT content_hash FROM news_items
--     WHERE content_hash IS NOT NULL GROUP BY 1 HAVING count(*)>1) x;
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_content_hash
  ON news_items(content_hash) WHERE content_hash IS NOT NULL;

COMMENT ON COLUMN news_items.category IS
  'Deterministic label: exploit|scam|regulation|research|shill|market|news (rugbrain.rss_classify)';
COMMENT ON COLUMN news_items.source_trust IS
  'Source-level trust weight 0.0-1.0 used by ranking; not per-item confidence';
COMMENT ON COLUMN news_items.content_hash IS
  'sha256(normalised title + guid|link) — collapses the same story reposted under another URL';

COMMIT;

-- Verify (expect 3 columns, 2 indexes):
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='news_items' AND column_name IN ('category','source_trust','content_hash');
--   SELECT indexname FROM pg_indexes WHERE tablename='news_items' AND indexname LIKE 'idx_news_%';

-- Legacy duplicate report (read-only; run before any cleanup):
--   SELECT count(*) AS dup_urls FROM (SELECT url FROM news_items GROUP BY url HAVING count(*)>1) x;
--   SELECT count(*) AS dup_titles FROM (SELECT lower(title) FROM news_items GROUP BY 1 HAVING count(*)>1) x;

-- ── DOWN ──────────────────────────────────────────────────────────────
-- BEGIN;
-- DROP INDEX IF EXISTS idx_news_content_hash;
-- DROP INDEX IF EXISTS idx_news_category;
-- ALTER TABLE news_items DROP COLUMN IF EXISTS content_hash;
-- ALTER TABLE news_items DROP COLUMN IF EXISTS source_trust;
-- ALTER TABLE news_items DROP COLUMN IF EXISTS category;
-- COMMIT;
