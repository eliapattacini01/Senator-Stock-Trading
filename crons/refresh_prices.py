"""
Render Cron Job — incremental price update, then leaderboard recompute.
Schedule: daily at 11:00 UTC (render.yaml), after the scrapers.

Downloads from Yahoo Finance only the days missing after each ticker's last
stored date (backend/price_updater.py), skipping tickers marked unavailable in
ticker_status, then pre-computes leaderboard_cache / top_stocks_cache.
The web app never downloads prices: this job is what keeps them fresh.
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

from backend.price_updater import sync_prices

sync_failed = False
try:
    sync_prices()
except Exception:
    LOGGER.exception("Price update failed — the leaderboard will use the prices already stored")
    sync_failed = True

# Pre-compute leaderboard and top-stocks cache now that prices are fresh
LOGGER.info("Triggering leaderboard pre-computation…")
import runpy
runpy.run_path(os.path.join(os.path.dirname(__file__), "compute_leaderboard.py"))

if sync_failed:
    sys.exit(1)
