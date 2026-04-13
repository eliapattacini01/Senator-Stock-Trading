"""
Render Cron Job — House PTR incremental scrape.
Schedule: daily at 03:00 UTC (render.yaml)
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
from Scraping.house_scraper import main as house_main
from Scraping.ingest import ingest_to_db


def get_from_year() -> int:
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("SELECT MAX(file_date) FROM transactions WHERE chamber = 'House'")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            since = row[0] - dt.timedelta(days=7)
            return min(since.year, dt.date.today().year)
    except Exception as exc:
        LOGGER.warning("Could not query latest file_date: %s", exc)
    return 2020


from_year = get_from_year()
LOGGER.info("House scrape starting (from_year=%d)", from_year)
df = house_main(from_year=from_year)
n  = ingest_to_db(df, chamber="House")
LOGGER.info("House scrape done: %d rows inserted", n)
