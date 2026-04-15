# Scraping Test Pipeline

This project is an asynchronous Python scraping pipeline for monitoring Indonesian health-related keywords across:

- X (Twitter), via Twikit
- News/web sources, via Scrapling

The pipeline can write results to:

- JSONL files (always enabled)
- PostgreSQL (enabled when DATABASE_URL is set)

It is designed for a sentiment-analysis workflow where data is first collected, then enriched later by a separate analysis stage.

## Why two scraping libraries are used

This project intentionally combines two tools with different strengths:

- Twikit
  - Used specifically for X (Twitter) authentication and keyword search.
  - Handles account session/cookies and API-like access to tweet objects.
- Scrapling
  - Used for general web/news scraping (Detik, Kompas, and future sources).
  - Strong fit for sites that may change markup or have anti-bot protections.

In short: Twikit is the X connector, while Scrapling is the broader web extraction layer.

## Scrapling notes relevant to this project

Based on current upstream capabilities (as summarized in project discussion), Scrapling offers:

- Adaptive extraction behavior for handling page structure changes over time.
- Multiple fetcher tiers for different anti-bot/JS complexity needs.
- Spider/crawl-oriented features (concurrency, persistence/checkpointing, streaming).
- MCP-oriented tooling for AI-assisted extraction workflows.

What this repo currently uses today:

- `scrapling[fetchers]` dependency.
- `AsyncFetcher.get(...)` in `WebScraper` with:
  - `stealthy_headers=True`
  - `impersonate="chrome"`

So the current implementation is a focused page fetch + CSS extraction flow, while keeping room to evolve into a deeper crawler if needed.

## Project files

- `pipeline.py`
  - Main application.
  - Contains all runtime components: config, scrapers, writers, orchestration, and entrypoint.
- `migrate.sql`
  - Database migration for PostgreSQL.
  - Creates schema objects, indexes, and helper views used by downstream analysis/reporting.
- `requirements.txt`
  - Python dependencies required by the pipeline.
- `.env.example`
  - Environment variable template for credentials and DB connection.

## How the pipeline works

The runtime flow in `pipeline.py` is:

1. Build `PipelineConfig`.
2. Initialize the pipeline components:
   - `XScraper` for X search
   - `WebScraper` for web/news scraping
   - `CompositeWriter` for output fan-out
3. Enable writers in `CompositeWriter.setup()`:
   - `FileWriter` (always)
   - `PostgresWriter` (only if `DATABASE_URL` is present)
4. Run social and web scraping concurrently using `asyncio.gather()`.
5. For each item:
   - Normalize into `ScrapedPost`
   - Deduplicate in-memory by `source:post_id`
   - Write to all enabled targets
6. Close writers and log final run stats.

### Key classes

- `PipelineConfig`
  - Holds keywords, X config, DB config, source definitions, output options, and rate limits.
- `ScrapedPost`
  - Unified data model used by all sources and outputs.
- `XScraper`
  - Authenticates using Twikit.
  - First run logs in with credentials and saves cookies to `x_cookies.json`.
  - Next runs reuse cookies for more stable sessions.
- `WebScraper`
  - Fetches configured sites with Scrapling and CSS selectors.
  - Filters scraped article titles by keyword match.
- `FileWriter`
  - Appends JSON objects to daily files in `output/`.
- `PostgresWriter`
  - Inserts rows into `scraped_posts`.
  - Uses `ON CONFLICT (source, post_id) DO NOTHING` for idempotent writes.
- `CompositeWriter`
  - Sends every record to all active writers.
- `ScrapingPipeline`
  - Orchestrates scraping, dedupe, writes, and stats.

## Database schema (`migrate.sql`)

Running the migration creates:

- Extensions
  - `pgcrypto` (UUID generation)
  - `pg_trgm` (fuzzy text search)
- Enum
  - `source_type_enum` with values `social` and `web`
- Main table
  - `scraped_posts`
  - Unique constraint on `(source, post_id)` for deduplication
- Indexes
  - For keyword/time filtering, source filtering, unanalyzed queue, trigram search, and JSONB metadata queries
- Views
  - `pending_analysis`: rows not yet sentiment-labeled
  - `sentiment_summary`: aggregated sentiment stats by keyword and source

## Setup

## 1) Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

## 2) Install dependencies

```bash
pip install -r requirements.txt
scrapling install
```

## 3) Configure environment variables

```bash
cp .env.example .env
```

Fill required values in `.env`:

- `X_USERNAME`
- `X_EMAIL`
- `X_PASSWORD`

Optional:

- `DATABASE_URL` (enables PostgreSQL output)

## 4) (Optional) Initialize PostgreSQL schema

If using DB output, run:

```bash
psql "$DATABASE_URL" -f migrate.sql
```

## 5) Run the pipeline

```bash
python pipeline.py
```

## Output behavior

- File output is always active.
- Files are written under `output/` and grouped by source and UTC date, for example:
  - `output/x_YYYYMMDD.jsonl`
  - `output/detik_health_YYYYMMDD.jsonl`
- PostgreSQL output is active only when `DATABASE_URL` is set.

## Configuration notes

Current code behavior in `pipeline.py`:

- Keywords and defaults are hardcoded in `main()`:
  - `keywords=["obat", "apotek", "farmasi", "resep dokter", "efek samping"]`
  - `x_max_results=100`
- Environment variables currently control:
  - X credentials (`X_USERNAME`, `X_EMAIL`, `X_PASSWORD`)
  - DB enablement/connection (`DATABASE_URL`)

`.env.example` includes additional optional keys for future overrides, but these are not fully wired into runtime parsing yet.

## Extending sources

To add another website source, append a config entry to `PipelineConfig.web_sources` with:

- `name`
- `url`
- `post_selector`
- `title_selector`
- `link_selector`

Then run the pipeline and verify extracted items in JSONL or database rows.

## Future scaling direction (optional)

If this project expands from keyword monitoring into full social listening, a natural path is:

1. Keep Twikit for X search and session handling.
2. Expand Scrapling usage for broader web/forum sources.
3. Add robust crawl-state persistence and retry orchestration.
4. Feed collected text into the existing sentiment stage and dashboard views.

## Copilot skill in this repo

This repository includes a local Scrapling skill for GitHub Copilot Chat:

- Location: `.github/skills/scrapling-official/SKILL.md`
- Purpose: provide best-practice guidance for choosing Scrapling fetch modes, spiders, and anti-bot handling during coding tasks.

Recommended usage:

1. Ask Copilot to use the Scrapling skill when working on scraping/crawling changes.
2. Follow the escalation flow from the skill:
  - `Fetcher` / `get` for simple pages
  - `DynamicFetcher` / `fetch` for JS-heavy pages
  - `StealthyFetcher` / `stealthy-fetch` for protected pages
  - `Spider` for multi-page crawling
3. When using Scrapling CLI extraction commands, include `--ai-targeted`.

## Typical use in a larger data workflow

1. Scrape and store raw posts/articles with this project.
2. Run a separate sentiment model to fill:
   - `sentiment_label`
   - `sentiment_score`
   - `analyzed_at`
3. Query `sentiment_summary` for dashboards or reporting.

## Troubleshooting

- X login/auth issues
  - Remove `x_cookies.json` and rerun to refresh session cookies.
- Empty web results
  - Check whether target site HTML or CSS selectors changed.
- No DB writes
  - Ensure `DATABASE_URL` is set and `migrate.sql` has been applied.
- Slow runs or request blocks
  - Increase delay values in `PipelineConfig` and reduce result volume.
