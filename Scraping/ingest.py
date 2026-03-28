"""
Ingest scraped transaction DataFrames into the PostgreSQL database.

Usage:
    from Scraping.ingest import ingest_to_db
    ingest_to_db(df, chamber='Senate')   # or 'House'
"""

import json
import logging
import os
import re
from datetime import date
from typing import Optional

import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger(__name__)

_BLACKLIST_PATH = os.path.join(os.path.dirname(__file__), "notebooks", "blacklist.json")
try:
    with open(_BLACKLIST_PATH) as _f:
        BLACKLIST: set = set(json.load(_f))
except FileNotFoundError:
    BLACKLIST = set()

ORDER_TYPE_MAP = {
    "purchase":        "BUY",
    "buy":             "BUY",
    "exchange":        "BUY",
    "sale":            "SELL",
    "sale (full)":     "SELL",
    "sale (partial)":  "SELL",
    "sell":            "SELL",
}


def parse_date(raw) -> Optional[date]:
    """Parse a date from various formats: YYYY-MM-DD, MM/DD/YYYY, datetime, etc."""
    if raw is None:
        return None
    if isinstance(raw, (pd.Timestamp,)):
        return raw.date() if not pd.isna(raw) else None
    try:
        return pd.to_datetime(str(raw), errors="raise", dayfirst=False).date()
    except Exception:
        return None


def parse_amount(tx_amount: str) -> Optional[int]:
    """Map a STOCK-Act transaction range to a midpoint integer estimate."""
    s = str(tx_amount).strip()
    if "1,001" in s and "15,000" in s:      return 8_000
    if "15,001" in s and "50,000" in s:     return 32_500
    if "50,001" in s and "100,000" in s:    return 75_000
    if "100,001" in s and "250,000" in s:   return 175_000
    if "250,001" in s and "500,000" in s:   return 375_000
    if "500,001" in s and "1,000,000" in s: return 750_000
    if "1,000,001" in s and "5,000,000" in s:   return 3_000_000
    if "5,000,001" in s and "25,000,000" in s:  return 15_000_000
    if "25,000,001" in s and "50,000,000" in s: return 37_500_000
    if "50,000,000" in s:                   return 50_000_000
    return None


def normalise_side(raw: str) -> Optional[str]:
    return ORDER_TYPE_MAP.get(raw.strip().lower())


def is_valid_ticker(ticker: str) -> bool:
    t = ticker.strip().upper()
    if not t or t in ("--", "UNKNOWN", "N/A", ""):
        return False
    if t in BLACKLIST:
        return False
    if not re.match(r"^[A-Z]{1,6}$", t):
        return False
    return True


def ingest_to_db(df: pd.DataFrame, chamber: str = "Senate") -> int:
    """
    Clean and upsert rows from ``df`` into the ``transactions`` table.

    Expected columns (same as Scraping/main.py output):
        tx_date, file_date, last_name, first_name,
        order_type, ticker, asset_name, tx_amount
    Optional extra column:
        chamber (overrides the ``chamber`` parameter per-row if present)

    Returns the number of rows inserted.
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")

    inserted = 0
    skipped = 0

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            # Ensure chamber column exists
            cur.execute("""
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS chamber VARCHAR(20) DEFAULT 'Senate'
            """)
            conn.commit()

            for _, row in df.iterrows():
                ticker = str(row.get("ticker", "")).strip().upper()
                if not is_valid_ticker(ticker):
                    skipped += 1
                    continue

                raw_side = str(row.get("order_type", ""))
                side = normalise_side(raw_side)
                if not side:
                    skipped += 1
                    continue

                first = re.sub(r'^[^a-zA-Z]+', '', str(row.get("first_name", "")).strip())
                last  = re.sub(r'^[^a-zA-Z]+', '', str(row.get("last_name",  "")).strip())
                full_name = f"{first} {last}".strip()
                if not full_name:
                    skipped += 1
                    continue

                tx_date     = parse_date(row.get("tx_date"))
                file_date   = parse_date(row.get("file_date"))
                asset_name  = str(row.get("asset_name", ""))[:500]
                tx_estimate = parse_amount(str(row.get("tx_amount", "")))
                row_chamber = str(row.get("chamber", chamber))

                # Skip rows where we couldn't parse the transaction date
                if tx_date is None:
                    skipped += 1
                    continue

                # Skip transactions with future dates (scraping artifacts)
                if tx_date > date.today():
                    skipped += 1
                    continue

                # Use a savepoint so a single bad row doesn't abort the whole tx
                try:
                    cur.execute("SAVEPOINT sp")
                    cur.execute(
                        """
                        INSERT INTO transactions
                            (full_name, ticker, side, tx_date, file_date,
                             tx_estimate, asset_name, chamber)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (full_name, ticker, side, tx_date, file_date,
                         tx_estimate, asset_name, row_chamber),
                    )
                    cur.execute("RELEASE SAVEPOINT sp")
                    inserted += 1
                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT sp")
                    LOGGER.warning("Row skipped (%s %s %s): %s", full_name, ticker, tx_date, exc)
                    skipped += 1

        conn.commit()

    LOGGER.info("Ingest complete: %d inserted, %d skipped", inserted, skipped)
    return inserted
