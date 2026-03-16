"""
Run all congressional trading scrapers and ingest into the database.

Usage:
    python -m Scraping.run_all_scrapers               # all sources
    python -m Scraping.run_all_scrapers --senate      # EFDS senate only
    python -m Scraping.run_all_scrapers --watcher     # Senate Stock Watcher (no key)
    python -m Scraping.run_all_scrapers --fmp         # FMP API (needs FMP_API_KEY)
    python -m Scraping.run_all_scrapers --finnhub     # Finnhub (needs FINNHUB_API_KEY)
    python -m Scraping.run_all_scrapers --house       # House EFTS scraper
    python -m Scraping.run_all_scrapers --quiverquant # QuiverQuant (no key, recent data)

Sources summary:
  1. EFDS senate    — official Senate gov site, 2012–present, requires no key
  2. EFTS house     — official House gov site, 2012–present, requires no key
  3. Senate Watcher — GitHub pre-scraped, 2014–2019, requires no key
  4. FMP            — free API key (financialmodelingprep.com), both chambers
  5. Finnhub        — free API key (finnhub.io), both chambers
  6. QuiverQuant    — no key, 1000 most recent trades, both chambers
"""

import argparse
import logging
import os
import sys

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Make sure the project root is on the path
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Scraping.ingest import ingest_to_db

LOGGER = logging.getLogger(__name__)

log_format = "[%(asctime)s %(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=log_format)


def _run(label: str, fn, *args, **kwargs) -> int:
    """Call fn(*args, **kwargs), ingest result, return number of rows inserted."""
    LOGGER.info("=" * 60)
    LOGGER.info("Starting: %s", label)
    try:
        df = fn(*args, **kwargs)
        if df is None or df.empty:
            LOGGER.warning("%s returned no data", label)
            return 0
        n = ingest_to_db(df)
        LOGGER.info("%s: ingested %d rows", label, n)
        return n
    except RuntimeError as exc:
        LOGGER.error("%s skipped: %s", label, exc)
        return 0
    except Exception as exc:
        LOGGER.exception("%s failed with unexpected error: %s", label, exc)
        return 0


def main():
    parser = argparse.ArgumentParser(description="Run congressional trading scrapers")
    parser.add_argument("--senate",      action="store_true", help="EFDS Senate scraper")
    parser.add_argument("--house",       action="store_true", help="EFTS House scraper")
    parser.add_argument("--watcher",     action="store_true", help="Senate Stock Watcher (GitHub, no key)")
    parser.add_argument("--fmp",         action="store_true", help="FMP API (needs FMP_API_KEY in .env)")
    parser.add_argument("--finnhub",     action="store_true", help="Finnhub API (needs FINNHUB_API_KEY)")
    parser.add_argument("--quiverquant", action="store_true", help="QuiverQuant (no key, recent trades)")
    args = parser.parse_args()

    # If no flags, run all
    run_all = not any(vars(args).values())

    total = 0

    # ── 1. Senate EFDS ──────────────────────────────────────────────────────
    if run_all or args.senate:
        from Scraping.main import main as senate_main
        total += _run("Senate EFDS", senate_main)

    # ── 2. House EFTS ───────────────────────────────────────────────────────
    if run_all or args.house:
        from Scraping.house_scraper import main as house_main
        total += _run("House EFTS", house_main)

    # ── 3. Senate Stock Watcher (free, GitHub) ───────────────────────────────
    if run_all or args.watcher:
        from Scraping.watcher_scraper import main as watcher_main
        total += _run("Senate Stock Watcher", watcher_main)

    # ── 4. FMP API ───────────────────────────────────────────────────────────
    if run_all or args.fmp:
        if os.environ.get("FMP_API_KEY"):
            from Scraping.fmp_scraper import main as fmp_main
            total += _run("FMP API", fmp_main)
        else:
            LOGGER.warning(
                "FMP skipped — set FMP_API_KEY in .env "
                "(free key at https://financialmodelingprep.com)"
            )

    # ── 5. Finnhub API ───────────────────────────────────────────────────────
    if run_all or args.finnhub:
        if os.environ.get("FINNHUB_API_KEY"):
            from Scraping.finnhub_scraper import main as finnhub_main
            total += _run("Finnhub API", finnhub_main)
        else:
            LOGGER.warning(
                "Finnhub skipped — set FINNHUB_API_KEY in .env "
                "(free key at https://finnhub.io)"
            )

    # ── 6. QuiverQuant (no key needed) ───────────────────────────────────────
    if run_all or args.quiverquant:
        from Scraping.quiverquant_scraper import main as qq_main
        total += _run("QuiverQuant", qq_main)

    LOGGER.info("=" * 60)
    LOGGER.info("All scrapers finished. Total rows inserted: %d", total)


if __name__ == "__main__":
    main()
