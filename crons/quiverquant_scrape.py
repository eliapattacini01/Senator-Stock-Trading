"""
Render Cron Job — QuiverQuant scrape (last 1000 trades, both chambers).
Schedule: daily at 03:30 UTC (render.yaml)

Also performs a coverage check: compares the oldest trade in the QuiverQuant
batch against the latest file_date already in the DB for each chamber.
If the batch overlaps with the DB, QuiverQuant alone is enough to stay current.
If there is a gap, the full Senate/House scrapers are needed.

Sets the module-level variable `covers_db` (bool) so run_all.py can decide
whether to skip the heavier scrapers.
"""
import sys
import os
import logging
import datetime as _dt

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

from backend.db import get_connection
from Scraping.quiverquant_scraper import main as qq_main
from Scraping.ingest import ingest_to_db

LOGGER.info("QuiverQuant scrape starting")
df = qq_main()
n  = ingest_to_db(df)
LOGGER.info("QuiverQuant scrape done: %d rows inserted", n)

# ── coverage check ─────────────────────────────────────────────────────────────
covers_db = False  # exported for run_all.py

if df.empty:
    LOGGER.warning("Coverage check skipped — empty QuiverQuant batch")
else:
    import pandas as pd

    # Oldest date in the QuiverQuant batch
    qq_dates = pd.to_datetime(df["file_date"], errors="coerce").dropna()
    if qq_dates.empty:
        qq_dates = pd.to_datetime(df["tx_date"], errors="coerce").dropna()

    if qq_dates.empty:
        LOGGER.warning("Coverage check skipped — no valid dates in batch")
    else:
        qq_oldest = qq_dates.min().date()
        qq_newest = qq_dates.max().date()

        # Latest file_date already in DB per chamber
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT chamber, MAX(file_date)
            FROM transactions
            WHERE file_date IS NOT NULL
            GROUP BY chamber
        """)
        db_latest = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()

        LOGGER.info("QuiverQuant batch: %s → %s (%d records)",
                    qq_oldest, qq_newest, len(df))
        LOGGER.info("DB latest file_date: %s", db_latest)

        gaps = []
        for chamber, latest in db_latest.items():
            if qq_oldest <= latest:
                LOGGER.info(
                    "[%s] COVERED — QQ oldest (%s) <= DB latest (%s)",
                    chamber, qq_oldest, latest,
                )
            else:
                gap_days = (qq_oldest - latest).days
                LOGGER.warning(
                    "[%s] GAP DETECTED — QQ oldest (%s) > DB latest (%s) by %d days. "
                    "Full scraper needed.",
                    chamber, qq_oldest, latest, gap_days,
                )
                gaps.append(chamber)

        covers_db = len(gaps) == 0

        if covers_db:
            LOGGER.info("Coverage OK — QuiverQuant batch fully covers the DB. "
                        "Full scrapers can be skipped.")
        else:
            LOGGER.warning("Coverage INCOMPLETE for: %s. Full scrapers recommended.", gaps)
