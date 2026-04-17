# PRD: Expanded Source Coverage

**Version:** 1.0
**Date:** 2026-04-16

---

## 1. Overview

### Background

The current scraping pipeline (`pipeline.py`) collects data from two source types:

- **X (Twitter)** — via Twikit, keyword search on authenticated sessions
- **Indonesian news/web** — via Scrapling, CSS selector extraction from Detik, Kompas, Liputan6, Antara, Suara

This covers X-based public discourse and mainstream news. It does not yet cover the platforms where a large portion of Indonesian brand conversation actually happens: Instagram, TikTok, YouTube, and e-commerce review pages. It also does not cover the full breadth of Indonesian online publishing (forums, additional news portals).

### Objective

Expand source coverage so that sentiment analysis reflects the actual landscape of Indonesian public discourse — not just X and five news sites.

---

## 2. Proposed Sources

Sources are grouped by implementation approach. This grouping directly maps to engineering effort.

### Group A — CSS Selector Extension (No New Scraper Class)

These sources are structurally identical to the existing `WebScraper` sources. Adding them requires only a new config entry in `PipelineConfig.web_sources` — no new code.

| Source | URL | Signal Type | Priority |
|---|---|---|---|
| Kumparan | `kumparan.com` | News + opinion, conversational tone | P1 |
| Tribunnews | `tribunnews.com` | High-volume tabloid, strong regional coverage | P1 |
| Tirto | `tirto.id` | Investigative/analytical, policy and social issues | P2 |
| Bisnis.com | `bisnis.com` | Business and market sentiment | P2 |

**Why these matter:** Detik and Kompas cover mainstream hard news well, but miss the high-volume tabloid and opinion ecosystem. Kumparan and Tribunnews together add significant volume. Tirto adds depth for policy topics. Bisnis.com fills the economic/corporate angle.

**Engineering note:** Each requires one CSS selector pass to identify the correct `post_selector`, `title_selector`, and `link_selector`. Estimated effort: 1–2 hours per source including verification.

---

### Group B — New Scraper Classes (Social Platforms)

These platforms require authentication, session management, or JS rendering. Each needs a new scraper class following the same interface as `XScraper`.

#### B1. Instagram

**Rationale:** Instagram is the primary platform for Indonesian consumer brand expression — product launches, reviews, and complaints surface here before anywhere else. Public post captions and comments on brand accounts are high-signal.

**Recommended library:** `instagrapi` — Python, cookie/session-based authentication, similar pattern to Twikit. Actively maintained.

**Data to collect:**
- Public post captions from brand accounts (hashtag search or account monitoring)
- Comments on public posts matching configured keywords

**`ScrapedPost` mapping:**

| Field | Source |
|---|---|
| `post_id` | Instagram media ID |
| `source` | `"instagram"` |
| `content` | Caption text or comment body |
| `author` | Username |
| `url` | Post permalink |
| `posted_at` | Post timestamp |
| `metadata` | `{ "type": "caption" | "comment", "media_id": "...", "like_count": N }` |

**Constraints:**
- Instagram rate-limits aggressively. Session rotation and delay management are required.
- Cookie sessions expire. Same cookie refresh pattern as `XScraper` (`x_cookies.json` → `ig_session.json`) is appropriate.
- Hashtag search is more reliable than keyword search on Instagram — configure keywords as hashtags in `PipelineConfig`.

**New env vars required:** `IG_USERNAME`, `IG_PASSWORD`

---

#### B2. TikTok

**Rationale:** TikTok is the dominant discovery platform for Indonesian Gen Z and increasingly for broader consumer segments. Video descriptions and comments carry strong sentiment signal, particularly for FMCG, lifestyle, and entertainment brands.

**Recommended library:** `pyktok` — no API key required, works on public content. Alternative: `tiktok-scraper` (Node.js, may need subprocess call or separate microservice).

**Data to collect:**
- Video descriptions from keyword/hashtag searches
- Comments on public videos matching configured keywords

**`ScrapedPost` mapping:**

| Field | Source |
|---|---|
| `post_id` | TikTok video ID |
| `source` | `"tiktok"` |
| `content` | Video description or comment body |
| `author` | Username |
| `url` | Video URL |
| `posted_at` | Video/comment timestamp |
| `metadata` | `{ "type": "description" | "comment", "video_id": "...", "play_count": N, "like_count": N }` |

**Constraints:**
- TikTok's scraping surface changes frequently. This is the most fragile source on this list — build with a circuit breaker so failures don't block the rest of the pipeline.
- No official search API for comment scraping. Keyword-to-hashtag mapping (same as Instagram) reduces surface area.
- Consider rate-limiting more conservatively than other sources (2–3x longer delays).

**New env vars required:** None for public content; optional `TIKTOK_SESSION_ID` for more stable access.

---

#### B3. YouTube Comments

**Rationale:** YouTube comments on brand, product, or campaign videos are underexplored for sentiment analysis but high-quality — longer, more considered than X posts, often more honest than Instagram comments. The YouTube Data API v3 is accessible, stable, and well-documented.

**Recommended library:** YouTube Data API v3 (official) via `google-api-python-client`. No scraping — clean REST. Alternative for no-API-key scenarios: `youtube-comment-downloader` (pure Python, no credentials).

**Preferred approach:** YouTube Data API v3. Free quota (10,000 units/day) is sufficient for MVP; comment list calls cost 1 unit per request.

**Data to collect:**
- Comments on public videos matching keyword search (via `search.list` + `commentThreads.list`)

**`ScrapedPost` mapping:**

| Field | Source |
|---|---|
| `post_id` | YouTube comment ID |
| `source` | `"youtube"` |
| `content` | Comment text |
| `author` | Display name |
| `url` | `https://youtube.com/watch?v={video_id}&lcdv={comment_id}` |
| `posted_at` | Comment published timestamp |
| `metadata` | `{ "video_id": "...", "video_title": "...", "like_count": N, "reply_count": N }` |

**Constraints:**
- API quota must be monitored. Add quota tracking to `PipelineConfig` and log usage per run.
- Comments are paginated via `nextPageToken`. Implement pagination loop with configurable max-pages limit.

**New env vars required:** `YOUTUBE_API_KEY`

---

#### B4. E-Commerce Reviews (Tokopedia / Shopee)

**Recommended approach:** Scrapling `DynamicFetcher` (JS rendering required for both platforms). Both sites are JS-heavy SPAs.

**Data to collect:**
- Product reviews for configured product/brand search terms
- Star rating alongside text (useful as a sentiment label cross-check)

**`ScrapedPost` mapping:**

| Field | Source |
|---|---|
| `post_id` | Review ID (from page DOM or URL) |
| `source` | `"tokopedia"` or `"shopee"` |
| `content` | Review text body |
| `author` | Reviewer username (often anonymized) |
| `url` | Product page URL |
| `posted_at` | Review date |
| `metadata` | `{ "star_rating": N, "product_name": "...", "verified_purchase": true/false }` |

**Constraints:**
- Both platforms use aggressive bot detection. Use `StealthyFetcher` from Scrapling (escalation tier above `AsyncFetcher`).
- Product URLs must be explicitly configured — there is no keyword search equivalent. `PipelineConfig` needs a new `ecommerce_sources` config section separate from `web_sources`.
- This is the highest-effort source. Recommend implementing after B1 and B3 are stable.

**New env vars required:** None (public reviews, no auth).

---

## 3. Implementation Approach

### Phasing

| Phase | Sources | Rationale |
|---|---|---|
| **Phase A** | Kumparan, Tribunnews, Tirto, Bisnis.com (Group A) | Zero new code — immediate coverage gain |
| **Phase B** | YouTube (B3) | Official API, most stable, lowest risk |
| **Phase C** | Instagram (B1) | High value, moderate fragility |
| **Phase D** | TikTok (B2), E-commerce (B4) | High value, highest fragility — build last |

### Interface Contract

All new scrapers must implement the same interface as `XScraper`:

```python
class NewPlatformScraper:
    async def setup(self) -> None: ...
    async def scrape(self, keywords: list[str]) -> list[ScrapedPost]: ...
    async def close(self) -> None: ...
```

The `ScrapingPipeline` orchestrator calls `asyncio.gather()` across all scrapers — new scrapers plug in without changes to orchestration logic.

### `source_type_enum` Extension

The current DB enum has two values: `social` and `web`. The migration will need a new value or a mapping:

| New Source | `source_type_enum` value |
|---|---|
| Instagram, TikTok, YouTube | `social` |
| Kumparan, Tribunnews, Tirto, Bisnis.com | `web` |
| Tokopedia, Shopee | `web` (reviews are web content, not social posts) |

No enum migration needed — existing values cover all new sources.

### `--source` CLI Flag Extension

Current values: `all`, `x`, `web`. Add granular options:

```
--source instagram
--source tiktok
--source youtube
--source ecommerce
```

`all` should include all configured sources. `web` should continue to mean news/web only (not e-commerce, to keep behavioral consistency).

### Circuit Breaker Pattern

TikTok and e-commerce scrapers are fragile. Wrap each in a try/except at the pipeline level that logs failure and continues — do not let one broken scraper abort the full run. This pattern should be applied retroactively to `XScraper` as well.

---

## 4. Configuration Changes

### New `PipelineConfig` fields

```python
# Existing
web_sources: list[WebSourceConfig]

# New
ecommerce_sources: list[EcommerceSourceConfig]  # Tokopedia/Shopee product URLs
instagram_config: InstagramConfig | None         # Credentials + hashtags
tiktok_config: TikTokConfig | None               # Session + hashtags
youtube_config: YouTubeConfig | None             # API key + search terms
```

### New `.env` variables

```
# Instagram
IG_USERNAME=
IG_PASSWORD=

# YouTube
YOUTUBE_API_KEY=

# TikTok (optional)
TIKTOK_SESSION_ID=
```

---

## 5. Database

No schema changes required. The existing `maven.scraped_posts` table and `(source, post_id)` unique constraint handle all new sources. The `source` column (varchar) accommodates any string value.

New indexes to add in a follow-up migration once data volume justifies it:

- Index on `source` for platform-specific queries (already exists per roadmap, verify)
- Index on `metadata->>'star_rating'` for e-commerce review filtering (add when Tokopedia/Shopee is live)

---

## 6. Output Files

Follows existing naming convention — one JSONL file per source per UTC day:

```
output/instagram_YYYYMMDD.jsonl
output/tiktok_YYYYMMDD.jsonl
output/youtube_YYYYMMDD.jsonl
output/tokopedia_YYYYMMDD.jsonl
output/shopee_YYYYMMDD.jsonl
output/kumparan_news_YYYYMMDD.jsonl
output/tribunnews_YYYYMMDD.jsonl
```

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| TikTok scraping surface changes | High | Medium | Circuit breaker; monitor upstream `pyktok` issues; fallback to `--source web` |
| Instagram session expiry / ban | Medium | Medium | Cookie refresh pattern (same as X); use secondary account for scraping |
| YouTube API quota exhaustion | Low | Low | Log quota per run; configurable max-pages limit; quota is generous at 10k/day |
| Shopee/Tokopedia bot detection | High | Low (deferred) | `StealthyFetcher`; implement last; accept higher failure rate at PoC stage |
| `source_type_enum` collision | None | None | Existing values cover all new sources — no migration needed |

---

## 8. Out of Scope

- **LinkedIn** — scraping is against ToS and technically difficult. Low signal-to-noise for Indonesian consumer brands. Defer indefinitely.
- **Reddit** — negligible Indonesian-language presence.
- **Facebook** — Graph API for public pages is severely restricted post-Cambridge Analytica. Scraping is high-risk. Revisit only if a specific client requires it.
- **Sentiment model retraining** — new sources will require new training data for Instagram captions, TikTok descriptions, and review text. This PRD covers data collection only. Model adaptation is a separate workstream (see roadmap item 8, ABSA).
