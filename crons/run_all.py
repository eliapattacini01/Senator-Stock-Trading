"""
Run all cron jobs in sequence.

Order:
  1. senate_scrape   — incremental scrape via Playwright (efdsearch.senate.gov)
  2. house_scrape    — incremental scrape via disclosures-clerk.house.gov
  3. refresh_prices  — update stock prices
  4. compute_leaderboard — pre-compute leaderboard + top-stocks cache
"""
import sys
import os
import logging
import runpy

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

CRONS_DIR = os.path.dirname(__file__)


def run(script, label):
    LOGGER.info("=" * 60)
    LOGGER.info("Starting: %s", label)
    LOGGER.info("=" * 60)
    try:
        runpy.run_path(os.path.join(CRONS_DIR, script), run_name="__main__")
        LOGGER.info("Done: %s", label)
    except Exception as exc:
        LOGGER.exception("FAILED: %s — %s", label, exc)


run("senate_scrape.py",        "Senate scraper")
run("house_scrape.py",         "House scraper")
run("refresh_prices.py",       "Price refresh + leaderboard")

LOGGER.info("=" * 60)
LOGGER.info("All jobs complete.")
LOGGER.info("=" * 60)
