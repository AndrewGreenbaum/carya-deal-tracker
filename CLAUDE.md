# Carya development notes

Setup and check commands are in [README.md](README.md).

## Pipeline

`src/harvester/` collects fund announcements, news, SEC filings, and other sources. `src/analyst/extractor.py` calls Claude and validates the extracted fields. `src/archivist/storage.py` handles company matching, duplicate detection, and persistence. `src/scheduler/jobs.py` runs scans and enrichment; `src/main.py` exposes the API.

Articles pass source, title, keyword, and content-hash filters before extraction. Haiku handles initial extraction, with optional Sonnet re-extraction for uncertain results. Post-processing checks names against the article, distinguishes lead investors from participants, and flags questionable amounts. Changing the prompt alone can affect these later checks, so add a regression case when fixing an extraction error.

Keep funding amounts separate from valuations. “$50M at a $500M valuation” is a $50M round. SEC filings may describe investment funds or special-purpose vehicles rather than operating companies; the SEC scraper and extractor both filter these cases.

## Fund matching

The registry and source settings are in [src/config/funds.py](src/config/funds.py). Some funds rely on external news rather than a dedicated site scraper.

Watch for these name collisions:

- Accel is unrelated to Accel Entertainment.
- GV is unrelated to the NYSE:GV ticker.
- Thrive Capital is unrelated to Thrive IT Services, Thrive Global, or Thrive Market.
- Sequoia coverage here excludes HongShan and Peak XV.
- BVP Forge is private equity and needs separate classification.

“Led by” and “co-led” are lead evidence; “backed by,” “participated,” and “joined” are not. Weak lead evidence should remain available for review rather than silently becoming a confirmed lead. Consumer AI, crypto, gaming, and social products are outside the enterprise AI filter.

## Storage and deduplication

Duplicate matching considers normalized company names, round type, amount, and date. There are separate paths for missing dates and amounts; do not reduce these to a single name comparison. See `find_duplicate_deal()` in [src/archivist/storage.py](src/archivist/storage.py) and the deduplication tests in `tests/`.

`dedup_key` groups company, round, and date; `amount_dedup_key` provides a second match when extraction disagrees on round labels. Persistent article hashes in `content_hashes` avoid reprocessing syndicated content across scans and expire after 30 days.

Migrations run at API startup. To inspect or apply them manually, activate the Python environment and run:

```sh
python -m alembic current
python -m alembic upgrade head
```

Cleanup scripts can change many records. Start with a dry run and inspect the proposed changes:

```sh
python scripts/cleanup_duplicate_deals.py --dry-run
python scripts/cleanup_fund_structures.py --dry-run
python scripts/backfill_enterprise_category.py --dry-run
```

## Enrichment and API usage

Website and LinkedIn enrichment use article data and Brave search. LinkedIn results need both a plausible profile URL and a name match; cache only validated results. The validators are in `src/common/url_utils.py` and the enrichment code is in `src/enrichment/`.

Automatic enrichment runs every third scan, with batches of 25 deals and 15 placeholder dates. These are limits in the scheduler, not a monthly cost guarantee. `brave_api_usage` records queries; `GET /brave/stats` reports usage and estimated cost. The budget and estimate settings are `brave_daily_query_budget`, `brave_monthly_cost_alert`, and `brave_cost_per_query` in `src/config/settings.py`. Check provider billing separately.

Useful endpoints include:

| Endpoint | Use |
|---|---|
| `GET /health` | API and database health |
| `GET /scheduler/status` | Scheduler state |
| `GET /scans` | Recent scan results |
| `GET /scrapers/diagnostics` | Source errors, circuit breakers, and timeouts |
| `GET /enrichment/stats` | Website and LinkedIn coverage |
| `GET /brave/stats` | Search query usage |

Protected endpoints expect an `X-API-Key` from the server's `API_KEYS` setting. Keep service keys in the backend environment.

## Dashboard

The React/Vite dashboard uses Tailwind CSS 4; its theme is in `frontend/src/index.css`.

Tracker column IDs are slugs, such as `watching` or `in_conversation`. An item's status must match its column slug. Columns load before drag sensors are enabled; optimistic moves roll back when the backend request fails. If dragging fails, check that the column request succeeded and inspect the `[Tracker]` and `[DND]` console messages.

The New tab uses the latest successful scan that saved deals. If it is empty, inspect `/scans` before changing the filters. Seen-deal state is kept in browser local storage.

Parse date-only values as local date components when displaying them. `new Date("2026-01-05")` uses UTC and can display the previous day in a local timezone.

## Troubleshooting scans

- No results from a source: inspect `SCRAPER_HEALTH_ALERT` messages and the site's current markup.
- External sources time out: collection and extraction have separate phases. Inspect `/scrapers/diagnostics` and Claude API errors before increasing timeouts.
- A scan stays running after a crash: the next scan marks runs older than 45 minutes as failed. The job timeout is 40 minutes.
- Missing content-hash or usage tables: check the applied Alembic revision.
- Wrong company, amount, or lead role: inspect the source article and extraction evidence, then add a regression test for the failure.

`CONFIDENCE_BANDS`, `REJECTION_STATS`, and `EXTRACTION_FILTER_STATS` log why records passed or failed extraction. `BRAVE_BUDGET_EXCEEDED` indicates that search usage reached the configured daily budget.

## Working with Claude Code

Use these notes as project context alongside the README. Run Python commands in the project's virtual environment, add regression tests for changes to extraction or matching, and keep credentials out of source. This file is also displayed by the dashboard's documentation page.
