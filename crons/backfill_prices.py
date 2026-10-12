"""
One-off price backfill — run it from your own machine (not on Render), so the
initial download doesn't count against Render's bandwidth.

    python crons/backfill_prices.py                  # apply migration + fill every gap
    python crons/backfill_prices.py --dry-run        # show what would change, write nothing
    python crons/backfill_prices.py --tickers AAPL,TWTR
    python crons/backfill_prices.py --full           # re-download the whole history of every ticker
    python crons/backfill_prices.py --migrate-only   # only apply database/migration_price_store.sql

Unlike the daily job it also retries tickers marked unavailable, and a ticker
Yahoo has no data for is marked unavailable after a single attempt.
Uses DATABASE_URL from .env / the environment.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")

from backend.db import get_connection
from backend.price_updater import apply_migration, sync_prices


def main() -> None:
    parser = argparse.ArgumentParser(description="One-off price backfill into the prices table.")
    parser.add_argument("--tickers", help="comma-separated list, default: every ticker in transactions")
    parser.add_argument("--full", action="store_true", help="re-download the whole history of every ticker")
    parser.add_argument("--dry-run", action="store_true", help="download and compare, write nothing")
    parser.add_argument("--migrate-only", action="store_true", help="only apply the SQL migration")
    args = parser.parse_args()

    if args.migrate_only:
        conn = get_connection()
        try:
            apply_migration(conn)
        finally:
            conn.close()
        logging.info("Migration applied.")
        return

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    sync_prices(
        include_unavailable=True,
        full=args.full,
        tickers=tickers,
        fail_threshold=1,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
