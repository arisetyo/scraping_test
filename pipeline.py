"""
Social Media Scraping Pipeline
Combines twikit (X/Twitter) + Scrapling (web sources) for Indonesian keyword monitoring.
Supports dual output: JSONL files and/or PostgreSQL.

Install:
    python -m pip install twikit scrapling[fetchers] aiofiles asyncpg python-dotenv
    scrapling install

Usage:
    Copy .env.example to .env, fill in your credentials, then:
    python pipeline.py
"""

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import aiofiles
import asyncpg
from dotenv import load_dotenv
from twikit import Client as TwitterClient
from scrapling.fetchers import AsyncFetcher

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


@dataclass
class PipelineConfig:
    # Keywords to search for (Bahasa Indonesia)
    keywords: list[str] = field(default_factory=lambda: ["obat", "apotek", "farmasi"])

    # X / twikit
    x_username: str = field(default_factory=lambda: os.getenv("X_USERNAME", ""))
    x_email: str = field(default_factory=lambda: os.getenv("X_EMAIL", ""))
    x_password: str = field(default_factory=lambda: os.getenv("X_PASSWORD", ""))
    x_cookie_file: str = "x_cookies.json"
    x_max_results: int = 50          # per keyword per run
    x_language: str = "id"           # Indonesian

    # PostgreSQL
    db_dsn: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    db_enabled: bool = field(default_factory=lambda: bool(os.getenv("DATABASE_URL")))
    db_pool_min: int = 2
    db_pool_max: int = 10

    # Web sources (Scrapling)
    web_sources: list[dict] = field(default_factory=lambda: [
        {
            "name": "detik_health",
            "url": "https://health.detik.com/",
            "post_selector": "article.list-content__item",
            "title_selector": "h3.media__title a::text",
            "link_selector": "h3.media__title a::attr(href)",
        },
        {
            "name": "kompas_health",
            "url": "https://health.kompas.com/",
            "post_selector": "div.articleList article",
            "title_selector": "h3 a::text",
            "link_selector": "h3 a::attr(href)",
        },
        # Add more sources here
    ])

    # File output (runs alongside DB if both are configured)
    output_dir: str = "output"
    output_format: str = "jsonl"     # "jsonl" or "json"

    # Rate limiting (seconds)
    delay_min: float = 1.5
    delay_max: float = 4.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ScrapedPost:
    source: str                      # "x" | source name
    source_type: str                 # "social" | "web"
    keyword: str
    post_id: str
    text: str
    url: str
    author: str = ""
    created_at: str = ""
    lang: str = "id"
    raw: dict = field(default_factory=dict)
    scraped_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# X scraper (twikit)
# ---------------------------------------------------------------------------

class XScraper:
    """
    Scrapes X (Twitter) posts by keyword using twikit session cookies.
    On first run, logs in with credentials and saves cookies.
    Subsequent runs reuse saved cookies — more stable and avoids login blocks.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.client = TwitterClient(language=config.x_language)
        self._authenticated = False

    async def authenticate(self):
        cookie_path = Path(self.config.x_cookie_file)

        if cookie_path.exists():
            logger.info("X: loading saved cookies from %s", cookie_path)
            self.client.load_cookies(str(cookie_path))
            self._authenticated = True
        else:
            logger.info("X: logging in as %s", self.config.x_username)
            await self.client.login(
                auth_info_1=self.config.x_username,
                auth_info_2=self.config.x_email,
                password=self.config.x_password,
            )
            self.client.save_cookies(str(cookie_path))
            logger.info("X: cookies saved to %s", cookie_path)
            self._authenticated = True

    async def search(self, keyword: str) -> AsyncIterator[ScrapedPost]:
        if not self._authenticated:
            await self.authenticate()

        logger.info("X: searching for '%s' (max %d)", keyword, self.config.x_max_results)

        try:
            tweets = await self.client.search_tweet(
                query=f"{keyword} lang:{self.config.x_language}",
                product="Latest",
                count=self.config.x_max_results,
            )
        except Exception as e:
            logger.error("X: search failed for '%s': %s", keyword, e)
            return

        for tweet in tweets:
            yield ScrapedPost(
                source="x",
                source_type="social",
                keyword=keyword,
                post_id=str(tweet.id),
                text=tweet.text,
                url=f"https://x.com/i/web/status/{tweet.id}",
                author=tweet.user.screen_name if tweet.user else "",
                created_at=str(tweet.created_at),
                lang=getattr(tweet, "lang", "id"),
                raw={
                    "retweet_count": getattr(tweet, "retweet_count", 0),
                    "like_count": getattr(tweet, "favorite_count", 0),
                    "reply_count": getattr(tweet, "reply_count", 0),
                },
            )

        await self._random_delay()

    async def _random_delay(self):
        delay = random.uniform(self.config.delay_min, self.config.delay_max)
        logger.debug("X: sleeping %.1fs", delay)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Web scraper (Scrapling)
# ---------------------------------------------------------------------------

class WebScraper:
    """
    Scrapes Indonesian health/news websites for keyword-matching articles
    using Scrapling's AsyncFetcher with TLS fingerprint impersonation.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config

    async def scrape_source(
        self,
        source: dict,
        keywords: list[str],
    ) -> AsyncIterator[ScrapedPost]:
        name = source["name"]
        url = source["url"]
        logger.info("Web: scraping %s (%s)", name, url)

        try:
            page = await AsyncFetcher.get(
                url,
                stealthy_headers=True,
                impersonate="chrome",
            )
        except Exception as e:
            logger.error("Web: failed to fetch %s: %s", url, e)
            return

        posts = page.css(source["post_selector"])
        logger.info("Web: found %d items on %s", len(posts), name)

        for post in posts:
            title_el = post.css(source["title_selector"])
            link_el = post.css(source["link_selector"])

            title = title_el.get("") if title_el else ""
            link = link_el.get("") if link_el else ""

            if not title:
                continue

            # Filter by keyword presence in title
            matched_keyword = next(
                (kw for kw in keywords if kw.lower() in title.lower()),
                None,
            )
            if not matched_keyword:
                continue

            yield ScrapedPost(
                source=name,
                source_type="web",
                keyword=matched_keyword,
                post_id=link or title[:60],
                text=title,
                url=link,
                raw={"source_url": url},
            )

        await self._random_delay()

    async def _random_delay(self):
        delay = random.uniform(self.config.delay_min, self.config.delay_max)
        logger.debug("Web: sleeping %.1fs", delay)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

class FileWriter:
    """
    Writes scraped posts to JSONL (one JSON object per line).
    JSONL is appendable and stream-friendly — good for incremental daily runs.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, aiofiles.threadpool.AsyncTextIOWrapper] = {}

    def _filepath(self, source: str) -> Path:
        ts = datetime.utcnow().strftime("%Y%m%d")
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
    """
    Writes scraped posts to PostgreSQL using asyncpg connection pool.
    Uses INSERT ... ON CONFLICT DO NOTHING for safe idempotent upserts —
    re-running the pipeline will never create duplicate rows.
    Writes to maven.scraped_posts explicitly to avoid search_path dependency.
    """

    def __init__(self, config: PipelineConfig):
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
                post.created_at or None,
                post.lang,
                json.dumps(post.raw, ensure_ascii=False),
                datetime.fromisoformat(post.scraped_at),
            )

    async def close(self):
        if self._pool:
            await self._pool.close()
            logger.info("PostgresWriter: connection pool closed")


class CompositeWriter:
    """
    Fans out writes to all enabled writers (file and/or postgres).
    The pipeline only talks to this — individual writers are transparent.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._writers: list[FileWriter | PostgresWriter] = []

    async def setup(self):
        # File writer is always active
        file_writer = FileWriter(self.config)
        self._writers.append(file_writer)
        logger.info("CompositeWriter: file output enabled → %s/", self.config.output_dir)

        # Postgres writer is opt-in via DATABASE_URL
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
# Pipeline orchestrator
# ---------------------------------------------------------------------------

class ScrapingPipeline:
    """
    Main pipeline. Runs X keyword search + web source scraping concurrently,
    deduplicates by post_id, and fans out writes to all enabled output targets.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.x_scraper = XScraper(config)
        self.web_scraper = WebScraper(config)
        self.writer = CompositeWriter(config)
        self._seen_ids: set[str] = set()
        self._stats: dict[str, int] = {"social": 0, "web": 0, "duplicates": 0}

    async def _process(self, post: ScrapedPost):
        dedup_key = f"{post.source}:{post.post_id}"
        if dedup_key in self._seen_ids:
            self._stats["duplicates"] += 1
            return
        self._seen_ids.add(dedup_key)
        await self.writer.write(post)
        self._stats[post.source_type] += 1

    async def run_x(self):
        for keyword in self.config.keywords:
            async for post in self.x_scraper.search(keyword):
                await self._process(post)

    async def run_web(self):
        for source in self.config.web_sources:
            async for post in self.web_scraper.scrape_source(source, self.config.keywords):
                await self._process(post)

    async def run(self):
        logger.info("Pipeline starting — keywords: %s", self.config.keywords)
        start = time.monotonic()

        await self.writer.setup()

        try:
            # Run X and web scraping concurrently
            await asyncio.gather(
                self.run_x(),
                self.run_web(),
            )
        finally:
            await self.writer.close()

        elapsed = time.monotonic() - start
        logger.info(
            "Pipeline done in %.1fs | Social posts: %d | Web posts: %d | Duplicates skipped: %d",
            elapsed,
            self._stats["social"],
            self._stats["web"],
            self._stats["duplicates"],
        )
        return self._stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    config = PipelineConfig(
        keywords=["obat", "apotek", "farmasi", "resep dokter", "efek samping"],
        # Credentials and DATABASE_URL are loaded automatically from .env
        x_max_results=100,
        output_dir="output",
        output_format="jsonl",
    )

    pipeline = ScrapingPipeline(config)
    await pipeline.run()


if __name__ == "__main__":
    asyncio.run(main())
