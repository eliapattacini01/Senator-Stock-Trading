"""
Render Cron Job — refresh stock prices for all tickers in the transactions table.
Schedule: daily at 06:00 UTC (render.yaml)

Fetches fresh price history from Stooq (yfinance fallback) for every distinct
ticker and saves it to the `prices` table in PostgreSQL.  This keeps the
portfolio-performance chart up-to-date even when nobody visits the site.
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

from backend.db import get_connection
from backend.performance import _download_from_stooq, _download_from_yfinance, _save_to_db

# Fetch all distinct valid tickers from the transactions table
conn = get_connection()
cur  = conn.cursor()
cur.execute("""
    SELECT DISTINCT ticker
    FROM transactions
    WHERE ticker IS NOT NULL
      AND ticker NOT IN ('--', 'UNKNOWN', '')
    ORDER BY ticker
""")
tickers = [r[0] for r in cur.fetchall()]
conn.close()

# Always include SPY for the benchmark line
if "SPY" not in tickers:
    tickers.append("SPY")

LOGGER.info("Refreshing prices for %d tickers", len(tickers))

ok = 0
failed = 0

for ticker in tickers:
    df = _download_from_stooq(ticker)
    if df is None:
        df = _download_from_yfinance(ticker)
    if df is not None and not df.empty:
        _save_to_db(ticker, df)
        ok += 1
    else:
        LOGGER.warning("No price data found for %s", ticker)
        failed += 1

LOGGER.info("Price refresh done: %d updated, %d failed", ok, failed)
