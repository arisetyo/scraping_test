---
description: "Workspace instructions for the Python scraping pipeline using Twikit, Scrapling, JSONL output, and optional PostgreSQL."
---

# Copilot Instructions For This Workspace

## Project purpose

This repository contains an asynchronous scraping pipeline for Indonesian health-related monitoring.

- X source is scraped via Twikit.
- Web/news sources are scraped via Scrapling.
- Output is written to JSONL files and optionally to PostgreSQL.

## Architectural rules

- Treat Twikit as X-specific and Scrapling as general web scraping.
- Preserve the unified data contract through `ScrapedPost` for all sources.
- Keep writes idempotent. For PostgreSQL, retain `ON CONFLICT (source, post_id) DO NOTHING` behavior.
- Preserve dual-output behavior through `CompositeWriter` unless asked to change it.
- Keep async-first patterns (`asyncio`, async writers, async fetchers).

## Editing guidance

- Prefer minimal, surgical edits in `pipeline.py`; avoid broad refactors unless requested.
- When adding new sources, follow the existing `web_sources` selector schema.
- If changing selectors, keep backward compatibility for existing source names where possible.
- Keep keyword filtering behavior explicit and case-insensitive.
- If adding environment variables, wire parsing in code (not only in `.env.example`) and document it in `README.md`.

## Dependency guidance

- Current core dependencies are:
	- `twikit`
	- `scrapling[fetchers]`
	- `aiofiles`
	- `asyncpg`
	- `python-dotenv`
- If introducing new libraries, justify the need and keep setup simple.

## Database guidance

- Schema is managed by `migrate.sql`.
- Keep compatibility with:
	- table `scraped_posts`
	- views `pending_analysis` and `sentiment_summary`
	- unique dedupe constraint `(source, post_id)`
- Prefer additive migrations and avoid destructive schema changes unless explicitly requested.

## Operational expectations

- Assume first run may need X login and cookie creation (`x_cookies.json`).
- Assume web sources may change markup over time; prefer resilient extraction updates.
- Preserve clear logging at each pipeline stage (auth, fetch, parse, write, summary).

## README synchronization rule

When behavior changes in any of these areas, update `README.md` in the same change:

- setup/install commands
- config/env variables
- source list or extraction behavior
- output and storage semantics
- database migration or query/view behavior
