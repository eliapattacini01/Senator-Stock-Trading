"""
Render Cron Job — QuiverQuant scrape (last 1000 trades, both chambers).
Schedule: daily at 03:30 UTC (render.yaml)
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

from Scraping.quiverquant_scraper import main as qq_main
from Scraping.ingest import ingest_to_db

LOGGER.info("QuiverQuant scrape starting")
df = qq_main()
n  = ingest_to_db(df)
LOGGER.info("QuiverQuant scrape done: %d rows inserted", n)
