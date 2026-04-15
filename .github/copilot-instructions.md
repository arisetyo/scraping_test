---
description: "Workspace instructions for the Python scraping pipeline using Twikit, Scrapling, JSONL output, and optional PostgreSQL."
---

# Copilot Instructions For This Workspace

## Project purpose

This repository contains an asynchronous scraping pipeline for generic keyword monitoring.

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

## Skill integration

- A workspace skill is available at `.github/skills/scrapling-official/SKILL.md`.
- Use this skill whenever tasks involve web scraping, crawling, anti-bot bypass, dynamic page extraction, or Scrapling CLI usage.
- Prefer the skill's escalation path when choosing fetch methods:
	- Fetcher/get for simple pages
	- Dynamic/fetch for JS-rendered pages
	- Stealthy/stealthy-fetch for protected pages
	- Spider for multi-page crawling
- For Scrapling CLI commands, include `--ai-targeted` as recommended by the skill.

## Database guidance

- Schema is managed by `migrate.sql`.
- Project objects live under schema `maven`.
- Keep compatibility with:
	- table `maven.scraped_posts`
	- views `maven.pending_analysis` and `maven.sentiment_summary`
	- unique dedupe constraint `(source, post_id)`
- Prefer additive migrations and avoid destructive schema changes unless explicitly requested.

## Operational expectations

- Always use `python3` instead of `python` for this repository.
- Always run Python and package-management commands inside the project virtual environment (`.venv`).
- Before running Python-related commands, verify the virtual environment is active; if not, activate it with `source .venv/bin/activate`.
- If `.venv` does not exist, create and activate it first:
	- `python3 -m venv .venv`
	- `source .venv/bin/activate`
- Use `python3 -m pip` instead of bare `pip` so dependencies are installed into the active virtual environment.
- Avoid global Python package installs for this repository.
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
