"""
Render Cron Job — Senate EFDS incremental scrape.
Schedule: daily at 02:00 UTC (render.yaml)
"""
import sys
import os
import datetime as dt
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

from backend.db import get_connection
from Scraping.main import main as senate_main
from Scraping.ingest import ingest_to_db


def get_since_date() -> str:
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("SELECT MAX(file_date) FROM transactions WHERE chamber = 'Senate'")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            since = row[0] - dt.timedelta(days=7)
            return since.strftime("%m/%d/%Y 00:00:00")
    except Exception as exc:
        LOGGER.warning("Could not query latest file_date: %s", exc)
    return "01/01/2012 00:00:00"


since = get_since_date()
LOGGER.info("Senate scrape starting (since %s)", since)
df = senate_main(since_date=since)
n  = ingest_to_db(df, chamber="Senate")
LOGGER.info("Senate scrape done: %d rows inserted", n)
