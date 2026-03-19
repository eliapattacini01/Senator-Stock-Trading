"""
Run all cron jobs in sequence.

Order:
  1. quiverquant_scrape  — fetch latest 1000 trades + coverage check
  2. senate_scrape       — only if QuiverQuant does NOT fully cover the DB
  3. house_scrape        — only if QuiverQuant does NOT fully cover the DB
  4. refresh_prices      — update stock prices + compute leaderboard cache

If QuiverQuant covers the DB (its oldest trade overlaps with what is already
stored), the heavier Senate/House scrapers are skipped automatically.
Pass --force-scrape to always run them regardless.
"""
import sys
import os
import logging
import runpy

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

CRONS_DIR  = os.path.dirname(__file__)
FORCE_SCRAPE = "--force-scrape" in sys.argv


def run(script, label):
    LOGGER.info("=" * 60)
    LOGGER.info("Starting: %s", label)
    LOGGER.info("=" * 60)
    try:
        result = runpy.run_path(os.path.join(CRONS_DIR, script), run_name="__main__")
        LOGGER.info("Done: %s", label)
        return result
    except Exception as exc:
        LOGGER.exception("FAILED: %s — %s", label, exc)
        return {}


# ── 1. QuiverQuant (always runs first) ────────────────────────────────────────
qq_globals = run("quiverquant_scrape.py", "QuiverQuant scraper")
covers_db  = qq_globals.get("covers_db", False)

# ── 2 & 3. Full scrapers (skipped if QQ covers the DB) ────────────────────────
if FORCE_SCRAPE:
    LOGGER.info("--force-scrape flag set — running full scrapers regardless.")
    run("senate_scrape.py", "Senate scraper")
    run("house_scrape.py",  "House scraper")
elif covers_db:
    LOGGER.info("QuiverQuant fully covers the DB — skipping Senate/House scrapers.")
else:
    LOGGER.warning("Gap detected — running full Senate/House scrapers.")
    run("senate_scrape.py", "Senate scraper")
    run("house_scrape.py",  "House scraper")

# ── 4. Price refresh + leaderboard ────────────────────────────────────────────
run("refresh_prices.py", "Price refresh + leaderboard")

LOGGER.info("=" * 60)
LOGGER.info("All jobs complete.")
LOGGER.info("=" * 60)
