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

import datetime as _dt
cutoff = _dt.date.today() - _dt.timedelta(days=730)

conn = get_connection()
cur  = conn.cursor()

# Ensure skip table exists
cur.execute("""
    CREATE TABLE IF NOT EXISTS ticker_skip (
        ticker     VARCHAR(20) PRIMARY KEY,
        failed_at  TIMESTAMP NOT NULL
    )
""")
conn.commit()

# Group 1 — tickers traded in the last 2 years (keep prices fresh)
cur.execute("""
    SELECT DISTINCT ticker FROM transactions
    WHERE ticker IS NOT NULL
      AND ticker NOT IN ('--', 'UNKNOWN', '')
      AND tx_date >= %s
      AND ticker NOT IN (SELECT ticker FROM ticker_skip)
    ORDER BY ticker
""", (cutoff,))
recent_tickers = {r[0] for r in cur.fetchall()}

# Group 2 — tickers never seen in the prices table (backfill for portfolio page)
cur.execute("""
    SELECT DISTINCT t.ticker FROM transactions t
    LEFT JOIN prices p ON p.ticker = t.ticker
    WHERE t.ticker IS NOT NULL
      AND t.ticker NOT IN ('--', 'UNKNOWN', '')
      AND p.ticker IS NULL
      AND t.ticker NOT IN (SELECT ticker FROM ticker_skip)
    ORDER BY t.ticker
""")
missing_tickers = {r[0] for r in cur.fetchall()}

conn.close()

tickers = sorted(recent_tickers | missing_tickers)

# Always include SPY for the benchmark line
if "SPY" not in tickers:
    tickers.append("SPY")

LOGGER.info(
    "Refreshing prices: %d recent + %d never-seen = %d total",
    len(recent_tickers), len(missing_tickers), len(tickers),
)

ok = 0
failed = 0
newly_skipped = 0

conn2 = get_connection()
cur2  = conn2.cursor()

for ticker in tickers:
    df = _download_from_stooq(ticker)
    if df is None:
        df = _download_from_yfinance(ticker)
    if df is not None and not df.empty:
        _save_to_db(ticker, df)
        ok += 1
    else:
        failed += 1
        # Only permanently skip tickers that have NEVER had price data (missing group).
        # Recent tickers (traded in last 2 years) may fail due to transient API errors —
        # don't blacklist them, just warn and retry next run.
        if ticker in missing_tickers:
            LOGGER.warning("No price data found for %s — adding to skip list", ticker)
            cur2.execute("""
                INSERT INTO ticker_skip (ticker, failed_at)
                VALUES (%s, NOW())
                ON CONFLICT (ticker) DO NOTHING
            """, (ticker,))
            conn2.commit()
            newly_skipped += 1
        else:
            LOGGER.warning("No price data found for %s — will retry next run", ticker)

conn2.close()
LOGGER.info("Price refresh done: %d updated, %d failed (%d added to skip list)", ok, failed, newly_skipped)

# Pre-compute leaderboard and top-stocks cache now that prices are fresh
LOGGER.info("Triggering leaderboard pre-computation…")
import runpy
runpy.run_path(os.path.join(os.path.dirname(__file__), "compute_leaderboard.py"))
