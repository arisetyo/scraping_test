# PRD: Refactor Scraping Pipeline Into Split Runners + Shared Services

## 1. Objective

Refactor the current monolithic runtime in `pipeline.py` into:

1. A dedicated pipeline script for scraping X.
2. A dedicated pipeline script for scraping web/news sources.
3. A shared services layer for:
	- console styling,
	- error reporting,
	- database persistence.

The refactor must preserve existing behavior and data/output contracts unless explicitly stated otherwise.

## 2. Decisions Already Fixed

1. Shared runtime concerns will be implemented as **separate classes with one facade**.
2. `python3 pipeline.py` will **not** be preserved as a backward-compatible wrapper. New scripts become the primary entry points.

## 3. Scope

### In Scope

1. Split execution paths into X-only and web-only scripts.
2. Extract shared concerns into reusable classes and expose them via one facade.
3. Keep all writers and data model behavior consistent with the current pipeline.
4. Update documentation for new run commands and architecture.

### Out of Scope

1. Adding new sources/platforms.
2. Redesigning `ScrapedPost` schema.
3. Changing database schema semantics.
4. Reworking keyword matching logic beyond necessary extraction.

## 4. Non-Negotiable Invariants

1. Keep unified data contract through `ScrapedPost`.
2. Keep asynchronous-first behavior (`asyncio`, async fetchers, async writers).
3. Keep idempotent DB writes with `ON CONFLICT (source, post_id) DO NOTHING`.
4. Keep dual-output model via a composite writer (file always-on, DB optional).
5. Keep keyword filtering explicit and case-insensitive.

## 5. Proposed Target Structure

```
scraping_test/
  pipeline_x.py                  # entrypoint: X scraping pipeline
  pipeline_web.py                # entrypoint: web scraping pipeline
  pipeline_shared/
	 __init__.py
	 config.py                    # PipelineConfig + env parsing helpers
	 models.py                    # ScrapedPost
	 logging_style.py             # ConsoleStyler
	 errors.py                    # ErrorReporter
	 persistence.py               # DBPersistence
	 services.py                  # RuntimeServices facade
	 writers.py                   # FileWriter, PostgresWriter, CompositeWriter
	 x_scraper.py                 # XScraper
	 web_scraper.py               # WebScraper
```

Notes:

1. Naming is flexible, but the responsibility split must be preserved.
2. If file count grows, split `writers.py` into a writers package in a follow-up pass.

## 6. Functional Requirements

### FR-1: Split Pipeline Entrypoints

1. `pipeline_x.py` runs X scraping flow only.
2. `pipeline_web.py` runs web scraping flow only.
3. Both scripts must support CLI overrides equivalent to current needs (`keywords`, `output-dir`, and relevant source-specific controls).

### FR-2: Shared Runtime Services

Implement separate classes:

1. `ConsoleStyler`
	- centralizes colored log formatting and setup.
2. `ErrorReporter`
	- centralizes friendly message mapping and reporting utilities.
3. `DBPersistence`
	- owns asyncpg pool lifecycle and insert operations.

And one facade:

1. `RuntimeServices`
	- composes the above classes.
	- provides one shared integration surface for both pipeline scripts.

### FR-3: Output Consistency

1. Preserve existing file output naming convention and JSONL append behavior.
2. Preserve DB table target (`maven.scraped_posts`) and idempotent insert rule.
3. Preserve per-run in-memory dedupe behavior by source/post key.

### FR-4: Error and Logging Consistency

1. Preserve existing user-facing error style for known Twikit login failures.
2. Ensure both pipelines produce consistent log format and severity handling.

## 7. Technical Migration Plan

### Phase A: Extract Shared Primitives

1. Move `PipelineConfig` and env parsing helpers into shared config module.
2. Move `ScrapedPost` into shared models module.
3. Keep behavior identical.

### Phase B: Extract Service Classes

1. Create `ConsoleStyler` from current formatter/setup logic.
2. Create `ErrorReporter` from current friendly error mapping and reporting patterns.
3. Create `DBPersistence` from current `PostgresWriter` pool + execute logic.
4. Add `RuntimeServices` facade for script integration.

### Phase C: Extract Scrapers and Writers

1. Move X scraper into dedicated module.
2. Move web scraper into dedicated module.
3. Move writers into shared writer module(s), preserving fan-out behavior.

### Phase D: Introduce Split Entrypoints

1. Add `pipeline_x.py`.
2. Add `pipeline_web.py`.
3. Wire each entrypoint to shared config, services, scraper, and writer stack.
4. Remove monolithic entrypoint responsibility from legacy `pipeline.py`.

### Phase E: Documentation Synchronization

1. Update `README.md` run instructions and architecture section in the same change.
2. Document operational notes for running separate scripts (including dedupe expectations).

## 8. Risks and Mitigations

1. Import cycles during extraction.
	- Mitigation: keep shared config/models modules dependency-light and foundational.
2. Behavioral drift between X and web scripts.
	- Mitigation: share one writer/services stack and centralized error/logging setup.
3. Inconsistent CLI support after split.
	- Mitigation: define and verify a minimum CLI parity checklist before merge.
4. Duplicate data across repeated runs when file-only mode is used.
	- Mitigation: document this as expected behavior; rely on DB conflict rule for persistent dedupe.

## 9. Acceptance Criteria

1. Two scripts exist and run independently:
	- X-only pipeline script.
	- web-only pipeline script.
2. Shared classes exist and are used by both scripts:
	- `ConsoleStyler`,
	- `ErrorReporter`,
	- `DBPersistence`,
	- `RuntimeServices` facade.
3. `ScrapedPost` contract remains compatible with previous output fields.
4. DB writes remain idempotent with `ON CONFLICT (source, post_id) DO NOTHING`.
5. README reflects new run model and architecture.

## 10. Verification Checklist

1. Run `python3 pipeline_x.py --help`.
2. Run `python3 pipeline_web.py --help`.
3. Execute one short X run and validate:
	- JSONL output exists,
	- optional DB rows inserted.
4. Execute one short web run and validate:
	- per-source JSONL output exists,
	- optional DB rows inserted.
5. Re-run with same inputs and confirm DB does not duplicate rows.
6. Trigger one controlled X auth failure and one web fetch failure; verify friendly errors and consistent console format.

## 11. Rollout Strategy

1. Implement in small PR-sized commits by phase (A-E).
2. Keep each phase behavior-preserving whenever possible.
3. After final verification, retire old monolithic execution path and treat split scripts as canonical.

## 12. Future Extensions (Post-Refactor)

1. Add source-specific pipeline scripts for additional platforms.
2. Introduce per-source scheduler orchestration.
3. Expand test coverage around service facade and writer contracts.
