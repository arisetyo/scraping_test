-- =============================================================================
-- Migration: Social Media Scraping Pipeline
-- Run once against your target database:
--     psql $DATABASE_URL -f migrate.sql
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- for trigram full-text search on text


-- ---------------------------------------------------------------------------
-- Enum types
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE source_type_enum AS ENUM ('social', 'web');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ---------------------------------------------------------------------------
-- Main table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS scraped_posts (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Source identification
    source          TEXT            NOT NULL,           -- "x", "detik_health", etc.
    source_type     source_type_enum NOT NULL,
    keyword         TEXT            NOT NULL,

    -- Content
    post_id         TEXT            NOT NULL,           -- original ID from the source platform
    text            TEXT            NOT NULL,
    url             TEXT,
    author          TEXT,
    lang            CHAR(10)        DEFAULT 'id',

    -- Timestamps
    created_at      TIMESTAMPTZ,                        -- when the post was originally published
    scraped_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Raw metadata (retweet/like counts, etc.)
    raw             JSONB           DEFAULT '{}'::jsonb,

    -- Sentiment (populated later by the analysis stage)
    sentiment_label TEXT,                               -- "positive" | "neutral" | "negative"
    sentiment_score NUMERIC(5, 4),                      -- confidence score 0.0000–1.0000
    analyzed_at     TIMESTAMPTZ,

    -- Deduplication constraint
    CONSTRAINT uq_source_post UNIQUE (source, post_id)
);

COMMENT ON TABLE scraped_posts IS
    'Raw posts scraped from X and Indonesian health/news websites for sentiment analysis.';

COMMENT ON COLUMN scraped_posts.raw IS
    'Platform-specific metadata: retweet counts, like counts, reply counts, source URL, etc.';

COMMENT ON COLUMN scraped_posts.sentiment_label IS
    'Populated by the sentiment analysis stage. NULL = not yet analyzed.';


-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Most common query: filter by keyword + time range
CREATE INDEX IF NOT EXISTS idx_posts_keyword_scraped
    ON scraped_posts (keyword, scraped_at DESC);

-- Filter by source
CREATE INDEX IF NOT EXISTS idx_posts_source
    ON scraped_posts (source, scraped_at DESC);

-- Filter unanalyzed rows (sentiment pipeline polling)
CREATE INDEX IF NOT EXISTS idx_posts_unanalyzed
    ON scraped_posts (scraped_at DESC)
    WHERE sentiment_label IS NULL;

-- Trigram index for fuzzy text search
CREATE INDEX IF NOT EXISTS idx_posts_text_trgm
    ON scraped_posts USING GIN (text gin_trgm_ops);

-- JSONB index for querying raw metadata
CREATE INDEX IF NOT EXISTS idx_posts_raw
    ON scraped_posts USING GIN (raw);


-- ---------------------------------------------------------------------------
-- Helper view: analysis-ready posts
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW pending_analysis AS
    SELECT id, source, source_type, keyword, post_id, text, lang, scraped_at
    FROM scraped_posts
    WHERE sentiment_label IS NULL
    ORDER BY scraped_at ASC;

COMMENT ON VIEW pending_analysis IS
    'Posts that have been scraped but not yet processed by the sentiment model.';


-- ---------------------------------------------------------------------------
-- Helper view: sentiment summary by keyword
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW sentiment_summary AS
    SELECT
        keyword,
        source,
        COUNT(*)                                            AS total,
        COUNT(*) FILTER (WHERE sentiment_label = 'positive') AS positive,
        COUNT(*) FILTER (WHERE sentiment_label = 'neutral')  AS neutral,
        COUNT(*) FILTER (WHERE sentiment_label = 'negative') AS negative,
        ROUND(
            COUNT(*) FILTER (WHERE sentiment_label = 'positive')::NUMERIC
            / NULLIF(COUNT(*), 0) * 100, 1
        )                                                   AS positive_pct,
        AVG(sentiment_score)                                AS avg_score,
        MAX(scraped_at)                                     AS last_scraped
    FROM scraped_posts
    WHERE sentiment_label IS NOT NULL
    GROUP BY keyword, source
    ORDER BY keyword, source;

COMMENT ON VIEW sentiment_summary IS
    'Aggregated sentiment breakdown per keyword and source — useful for dashboards.';
