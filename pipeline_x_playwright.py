"""
X (Twitter) Scraping Pipeline — Playwright / Scrapling edition.

Scrapes X search results using Scrapling's StealthyFetcher (Playwright-based),
bypassing twikit's broken API by loading X's web UI directly with browser cookies.

Reuses the same data model, writers, and output infrastructure as the main pipeline.

Requirements (already in requirements.txt):
    scrapling[fetchers]  aiofiles  asyncpg  python-dotenv
    scrapling install    # one-time browser setup

Usage:
    python3 pipeline_x_playwright.py --keywords "economy,politics,startup"
"""

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote_plus

import aiofiles
import asyncpg
from dotenv import load_dotenv
from scrapling.fetchers import StealthyFetcher

load_dotenv()

# ---------------------------------------------------------------------------
# Logging (same format as main pipeline)
# ---------------------------------------------------------------------------

class ColorLogFormatter(logging.Formatter):
    RESET = "\033[0m"
    LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }

    def __init__(self, use_color: bool):
        super().__init__(fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        if self.use_color:
            original_levelname = record.levelname
            color = self.LEVEL_COLORS.get(record.levelno, "")
            record.levelname = f"{color}{original_levelname}{self.RESET}"
            try:
                return super().format(record)
            finally:
                record.levelname = original_levelname
        return super().format(record)


def configure_logging() -> None:
    use_color = sys.stderr.isatty() and not os.getenv("NO_COLOR")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(ColorLogFormatter(use_color=use_color))
    root_logger.addHandler(handler)


configure_logging()
logger = logging.getLogger("pipeline_x_pw")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _keywords_from_env() -> list[str]:
    return _parse_keywords(os.getenv("KEYWORDS")) or ["news", "policy", "technology"]


def _int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r. Falling back to default %d.", name, raw, default)
        return default
    if parsed <= 0:
        logger.warning("Non-positive %s=%r. Falling back to default %d.", name, raw, default)
        return default
    return parsed


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class XPlaywrightConfig:
    keywords: list[str] = field(default_factory=_keywords_from_env)
    x_cookie_file: str = "x_cookies.json"
    x_max_results: int = field(default_factory=lambda: _int_from_env("X_MAX_RESULTS", 50))
    x_language: str = "id"

    # PostgreSQL
    db_dsn: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    db_enabled: bool = field(default_factory=lambda: bool(os.getenv("DATABASE_URL")))
    db_pool_min: int = 2
    db_pool_max: int = 10

    # File output
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "output"))
    output_format: str = "jsonl"

    # Rate limiting (seconds)
    delay_min: float = 2.0
    delay_max: float = 5.0


# ---------------------------------------------------------------------------
# Data model (mirrors main pipeline's ScrapedPost)
# ---------------------------------------------------------------------------

@dataclass
class ScrapedPost:
    source: str
    source_type: str
    keyword: str
    post_id: str
    text: str
    url: str
    author: str = ""
    created_at: str = ""
    lang: str = "id"
    raw: dict = field(default_factory=dict)
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# X browser scraper
# ---------------------------------------------------------------------------

class XBrowserScraper:
    """
    Scrapes X search results using Scrapling's StealthyFetcher (Playwright).
    Loads X's web UI with browser-exported cookies, scrolls the timeline,
    and extracts tweet data from the rendered DOM.
    """

    def __init__(self, config: XPlaywrightConfig):
        self.config = config
        self._cookies: list[dict] | None = None

    def _load_cookies(self):
        cookie_path = Path(self.config.x_cookie_file)
        if not cookie_path.exists():
            raise RuntimeError(
                f"X browser scraping requires {self.config.x_cookie_file}. "
                "Log in to x.com in your browser, export cookies as a flat JSON "
                "dict ({{name: value, ...}}), and save the file."
            )
        with open(cookie_path, encoding="utf-8") as f:
            raw = json.load(f)
        self._cookies = [
            {"name": k, "value": v, "domain": ".x.com", "path": "/"}
            for k, v in raw.items()
        ]
        logger.info("Loaded %d cookies from %s", len(self._cookies), cookie_path)

    async def search(self, keyword: str) -> AsyncIterator[ScrapedPost]:
        if self._cookies is None:
            self._load_cookies()

        query = f"{keyword} lang:{self.config.x_language}"
        url = f"https://x.com/search?q={quote_plus(query)}&src=typed_query&f=live"
        logger.info("Searching for '%s' (max %d)", keyword, self.config.x_max_results)

        scroll_rounds = min(max(1, self.config.x_max_results // 20), 5)

        async def scroll_page(page):
            for i in range(scroll_rounds):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)

        try:
            page = await StealthyFetcher.async_fetch(
                url,
                headless=True,
                cookies=self._cookies,
                network_idle=True,
                wait_selector='[data-testid="tweet"]',
                page_action=scroll_page,
                google_search=False,
                block_ads=True,
                timeout=30000,
            )
        except Exception as e:
            logger.error("Failed to load search for '%s': %s", keyword, e)
            return

        tweets = page.css('article[data-testid="tweet"]')
        logger.info("Found %d tweets on page for '%s'", len(tweets), keyword)

        count = 0
        for tweet_el in tweets:
            if count >= self.config.x_max_results:
                break

            # --- tweet text ---
            text_parts = tweet_el.css('[data-testid="tweetText"] ::text').getall()
            text = " ".join(t.strip() for t in text_parts if t.strip())
            if not text:
                continue

            # --- tweet URL & post ID ---
            tweet_url = ""
            post_id = ""
            for href in tweet_el.css('a[href*="/status/"]::attr(href)').getall():
                parts = href.split("/status/")
                if len(parts) == 2:
                    raw_id = parts[1].strip("/").split("?")[0].split("/")[0]
                    if raw_id.isdigit():
                        post_id = raw_id
                        tweet_url = f"https://x.com{href}" if href.startswith("/") else href
                        break
            if not post_id:
                continue

            # --- author handle ---
            author = ""
            for href in tweet_el.css('a[href]::attr(href)').getall():
                if href.startswith("/") and "/" not in href[1:] and len(href) > 1:
                    author = href[1:]
                    break

            # --- timestamp ---
            created_at = tweet_el.css('time[datetime]::attr(datetime)').get() or ""

            count += 1
            yield ScrapedPost(
                source="x",
                source_type="social",
                keyword=keyword,
                post_id=post_id,
                text=text,
                url=tweet_url,
                author=author,
                created_at=created_at,
                lang=self.config.x_language,
                raw={},
            )

        logger.info("Yielded %d posts for '%s'", count, keyword)
        await self._random_delay()

    async def _random_delay(self):
        delay = random.uniform(self.config.delay_min, self.config.delay_max)
        logger.debug("Sleeping %.1fs", delay)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Output writers (same as main pipeline)
# ---------------------------------------------------------------------------

class FileWriter:
    def __init__(self, config: XPlaywrightConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, aiofiles.threadpool.AsyncTextIOWrapper] = {}

    def _filepath(self, source: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        ext = "jsonl" if self.config.output_format == "jsonl" else "json"
        return self.output_dir / f"{source}_{ts}.{ext}"

    async def write(self, post: ScrapedPost):
        key = post.source
        if key not in self._files:
            path = self._filepath(key)
            self._files[key] = await aiofiles.open(path, mode="a", encoding="utf-8")
            logger.info("FileWriter: opened %s", path)
        line = json.dumps(asdict(post), ensure_ascii=False)
        await self._files[key].write(line + "\n")

    async def close(self):
        for f in self._files.values():
            await f.close()
        self._files.clear()


class PostgresWriter:
    def __init__(self, config: XPlaywrightConfig):
        self.config = config
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        logger.info("PostgresWriter: connecting to database")
        self._pool = await asyncpg.create_pool(
            dsn=self.config.db_dsn,
            min_size=self.config.db_pool_min,
            max_size=self.config.db_pool_max,
        )
        logger.info("PostgresWriter: connection pool ready")

    async def write(self, post: ScrapedPost):
        if not self._pool:
            raise RuntimeError("PostgresWriter.connect() was not called")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO maven.scraped_posts (
                    source, source_type, keyword, post_id, text, url,
                    author, created_at, lang, raw, scraped_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9, $10::jsonb, $11
                )
                ON CONFLICT (source, post_id) DO NOTHING
                """,
                post.source,
                post.source_type,
                post.keyword,
                post.post_id,
                post.text,
                post.url,
                post.author,
                datetime.fromisoformat(post.created_at.replace("Z", "+00:00")) if post.created_at else None,
                post.lang,
                json.dumps(post.raw, ensure_ascii=False),
                datetime.fromisoformat(post.scraped_at),
            )

    async def close(self):
        if self._pool:
            await self._pool.close()
            logger.info("PostgresWriter: connection pool closed")


class CompositeWriter:
    def __init__(self, config: XPlaywrightConfig):
        self.config = config
        self._writers: list[FileWriter | PostgresWriter] = []

    async def setup(self):
        file_writer = FileWriter(self.config)
        self._writers.append(file_writer)
        logger.info("CompositeWriter: file output enabled → %s/", self.config.output_dir)

        if self.config.db_enabled:
            pg_writer = PostgresWriter(self.config)
            await pg_writer.connect()
            self._writers.append(pg_writer)
            logger.info("CompositeWriter: PostgreSQL output enabled")
        else:
            logger.info("CompositeWriter: PostgreSQL disabled (no DATABASE_URL set)")

    async def write(self, post: ScrapedPost):
        await asyncio.gather(*[w.write(post) for w in self._writers])

    async def close(self):
        await asyncio.gather(*[w.close() for w in self._writers])


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class XPlaywrightPipeline:
    def __init__(self, config: XPlaywrightConfig):
        self.config = config
        self.scraper = XBrowserScraper(config)
        self.writer = CompositeWriter(config)
        self._seen_ids: set[str] = set()
        self._stats: dict[str, int] = {"total": 0, "duplicates": 0}

    async def _process(self, post: ScrapedPost):
        dedup_key = f"{post.source}:{post.post_id}"
        if dedup_key in self._seen_ids:
            self._stats["duplicates"] += 1
            return
        self._seen_ids.add(dedup_key)
        await self.writer.write(post)
        self._stats["total"] += 1

    async def run(self):
        logger.info("X Playwright pipeline starting — keywords: %s", self.config.keywords)
        start = time.monotonic()

        await self.writer.setup()

        try:
            for keyword in self.config.keywords:
                async for post in self.scraper.search(keyword):
                    await self._process(post)
        except Exception as e:
            logger.error("X scraping stopped: %s", e)
        finally:
            await self.writer.close()

        elapsed = time.monotonic() - start
        logger.info("Pipeline done in %.1fs", elapsed)
        logger.info(
            "\n"
            "+----------------------+--------+\n"
            "| Metric               | Value  |\n"
            "+----------------------+--------+\n"
            "| Posts scraped         | %-6d |\n"
            "| Duplicates skipped   | %-6d |\n"
            "+----------------------+--------+",
            self._stats["total"],
            self._stats["duplicates"],
        )
        return self._stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description="Scrape X via Playwright (browser-based). Requires x_cookies.json."
    )
    parser.add_argument(
        "--keywords",
        type=str,
        help="Comma-separated keywords. If omitted, falls back to KEYWORDS env.",
    )
    parser.add_argument(
        "--x-max-results",
        type=int,
        help="Max X posts per keyword. If omitted, falls back to X_MAX_RESULTS env.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Output directory for file output. If omitted, falls back to OUTPUT_DIR env.",
    )
    parser.add_argument(
        "--cookie-file",
        type=str,
        help="Path to cookie JSON file (default: x_cookies.json).",
    )
    args = parser.parse_args()

    config = XPlaywrightConfig()

    if args.keywords is not None:
        cli_keywords = _parse_keywords(args.keywords)
        if not cli_keywords:
            parser.error("--keywords was provided but no valid keywords were parsed")
        config.keywords = cli_keywords

    if args.x_max_results is not None:
        if args.x_max_results <= 0:
            parser.error("--x-max-results must be a positive integer")
        config.x_max_results = args.x_max_results

    if args.output_dir is not None:
        cli_output_dir = args.output_dir.strip()
        if not cli_output_dir:
            parser.error("--output-dir must not be empty")
        config.output_dir = cli_output_dir

    if args.cookie_file is not None:
        config.x_cookie_file = args.cookie_file

    pipeline = XPlaywrightPipeline(config)
    await pipeline.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error("Fatal error: %s", e)
        sys.exit(1)
