# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Requires DATABASE_URL set in .env or environment
uvicorn backend.main:app --reload --port 5000
```

The app auto-creates/migrates tables on startup via `_ensure_schema()` in `backend/main.py`.

## Running Cron Jobs Manually

```bash
python crons/senate_scrape.py
python crons/house_scrape.py
python crons/quiverquant_scrape.py
python crons/refresh_prices.py        # incremental price update, then triggers compute_leaderboard.py
python crons/compute_leaderboard.py   # standalone leaderboard recompute
```

One-off price backfill (run from a local machine, not on Render):

```bash
python crons/backfill_prices.py --dry-run        # show what would change
python crons/backfill_prices.py                  # migrate + fill every gap, retry unavailable tickers
python crons/backfill_prices.py --tickers AAPL   # only some tickers; --full re-downloads everything
python crons/backfill_prices.py --migrate-only   # only apply database/migration_price_store.sql
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest   # tests/ — fake DB, no DATABASE_URL needed
```

## Running the Name/Data Cleanup Script

```bash
python fix_names.py   # strips leading dots, removes duplicates, fixes bad dates
```

## Architecture Overview

**Data flow:** Scrapers → `ingest_to_db()` → `transactions` table → price refresh → cache tables → API → frontend

### Backend (`backend/`)
- `main.py` — FastAPI app. All API endpoints live here. Also contains name-normalisation helpers (`_norm`, `_norm_key`, `_name_variants`) used to deduplicate name variants (e.g. "Shelley M. Capito" vs "Shelley Moore Capito"). On non-Render environments, also starts APScheduler.
- `performance.py` — Portfolio simulation engine. Simulates a buy-and-hold portfolio from transaction history and computes period returns vs SPY benchmark. Reads prices **only** from the `prices` table (per-process cache, `PRICE_CACHE_TTL` / `PRICE_CACHE_MAX_TICKERS`); it never downloads.
- `price_updater.py` — Yahoo Finance (yfinance) → `prices` sync, used only by `crons/refresh_prices.py` and `crons/backfill_prices.py`. Incremental per ticker, batched `yf.download`, re-downloads a ticker in full when Yahoo re-adjusts its history (split/dividend), marks delisted tickers in `ticker_status`.
- `db.py` — `get_connection()`: pooled connection in the web app (`psycopg_pool` with `check=ConnectionPool.check_connection`, so connections Neon closed are replaced), direct connection in scripts.
- `scheduler.py` — APScheduler config for local dev (Render uses cron jobs instead).

### Scraping (`Scraping/`)
- `senate_scraper.py` / `house_scraper.py` — Primary scrapers. Senate uses EFDS XML/JSON API; House scrapes PTR PDFs from disclosures-clerk.house.gov.
- `quiverquant_scraper.py` — Fast scraper covering both chambers from QuiverQuant.
- `ingest.py` — Shared ingestion function `ingest_to_db(df, chamber)`. Normalises sides (Purchase→BUY, Sale→SELL), parses STOCK Act amount ranges into midpoint estimates, strips leading non-alpha junk from names, and upserts to `transactions`.

### Crons (`crons/`)
Thin wrappers around scrapers. Each cron determines the incremental start date from the DB before calling the scraper and ingesting results.

### Frontend (`frontend/`)
Static HTML/JS pages. All data fetched from the FastAPI backend. Pages: `index.html` (trades browser), `leaderboard.html`, `portfolio.html`, `timeseries.html`, `monthlychar.html`, `chat.html`.

## Database Tables

| Table | Notes |
|-------|-------|
| `transactions` | Core data. `chamber` is `'Senate'` or `'House'`. `side` is `'BUY'` or `'SELL'`. `tx_estimate` is midpoint integer (dollars). |
| `prices` | `(ticker, date, price)` — daily adjusted close (yfinance `auto_adjust=True`). SPY always present as benchmark. Written only by the price crons. |
| `ticker_status` | Per-ticker download state; `unavailable = TRUE` tickers are skipped by the daily job. Schema in `database/migration_price_store.sql` (replaces the legacy `ticker_skip`). |
| `leaderboard_cache` | Pre-computed returns per member per period (`1M`, `3M`, `YTD`, `1Y`). Must be recomputed after price refresh. |
| `top_stocks_cache` | Pre-computed top-5 bought/sold tickers per period. Recomputed alongside leaderboard. |

## Deployment (Render)

Defined in `render.yaml`. Web service runs `uvicorn backend.main:app`. Four daily cron jobs replace APScheduler (scheduler is disabled when `RENDER=true` env var is set). Cron order matters: senate/house scrapers run first, then QuiverQuant gap-fill, then price refresh which triggers leaderboard recompute.

## Key Gotchas

- **Name deduplication:** The same person can appear under multiple name variants in the DB. `_norm_key()` groups them by `(first_token, last_token)`. When querying by person, always use `_name_variants()` to get all variants.
- **leaderboard_cache dirty data:** If names are fixed in `transactions`, the same fix must be applied to `leaderboard_cache` (it's a separate table with its own `full_name` column).
- **House name artifacts:** House PTR HTML can produce leading `.` or `..` in names. `ingest.py` strips these with `re.sub(r'^[^a-zA-Z]+', '', ...)`. Run `fix_names.py` to clean existing DB data.
- **No downloads in the request path:** API endpoints must never call yfinance/Stooq or any external price source (a bot scanning `/portfolio/performance` once burned the Render bandwidth quota). `tests/test_performance_no_yfinance.py` enforces it.
- **Rate limiting / client IP:** Render's edge is Cloudflare, so `_client_ip()` keys limits on `CF-Connecting-IP` (then `X-Forwarded-For`). Env: `BLOCKED_IPS` (comma-separated, 403), `LOG_CLIENT_IP_HEADERS=1` (debug log), `PERF_CACHE_TTL`, `CACHE_MAX_ENTRIES`. Caches and limits are per worker process.
- **Tests:** `tests/` is a pytest suite; `test_chat.py` is still an ad-hoc script.
