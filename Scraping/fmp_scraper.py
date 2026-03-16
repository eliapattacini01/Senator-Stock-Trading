"""
Financial Modeling Prep (FMP) congressional trading scraper.

Free API key: sign up at https://financialmodelingprep.com (no credit card needed).
Add to .env:  FMP_API_KEY=your_key_here

Free tier limits: 250 API calls/day.
Coverage: Both Senate and House, ongoing updates, both chambers.

Endpoints used:
  Senate: GET https://financialmodelingprep.com/stable/senate-trading
  House:  GET https://financialmodelingprep.com/stable/house-trading
"""

import logging
import os
import time
from typing import Literal

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger(__name__)

FMP_BASE   = "https://financialmodelingprep.com/stable"
RATE_LIMIT = 1.5  # seconds between requests (free tier)

REPORT_COL_NAMES = [
    "tx_date", "file_date", "last_name", "first_name",
    "order_type", "ticker", "asset_name", "tx_amount", "chamber",
]

_TYPE_MAP = {
    "buy":          "Purchase",
    "purchase":     "Purchase",
    "sell":         "Sale (Full)",
    "sale":         "Sale (Full)",
    "sale_full":    "Sale (Full)",
    "sale_partial": "Sale (Partial)",
    "exchange":     "Exchange",
}


def _get_api_key() -> str:
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FMP_API_KEY not set. Get a free key at https://financialmodelingprep.com "
            "and add FMP_API_KEY=your_key to your .env file."
        )
    return key


def _normalise_type(raw: str) -> str:
    return _TYPE_MAP.get(raw.strip().lower(), raw.strip())


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (full, "")


def _fetch_page(endpoint: str, page: int, api_key: str) -> list:
    url = f"{FMP_BASE}/{endpoint}"
    params = {"apikey": api_key, "page": page, "limit": 250}
    time.sleep(RATE_LIMIT)
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "Error Message" in data:
        raise RuntimeError(f"FMP API error: {data['Error Message']}")
    return data if isinstance(data, list) else []


def _fetch_all(endpoint: str, chamber: str, api_key: str) -> pd.DataFrame:
    """Paginate through all pages of a FMP congressional trading endpoint."""
    LOGGER.info("Fetching FMP %s data (endpoint: %s)…", chamber, endpoint)
    all_rows = []
    page = 0

    while True:
        batch = _fetch_page(endpoint, page, api_key)
        if not batch:
            break

        for rec in batch:
            # FMP field names vary slightly between stable/legacy endpoints
            ticker = str(
                rec.get("symbol") or rec.get("ticker") or ""
            ).strip().upper()
            if not ticker or ticker in ("--", "N/A", ""):
                continue

            # Name fields: senator / representative / name
            person = str(
                rec.get("senator") or rec.get("representative") or rec.get("name") or ""
            ).strip()
            if not person:
                continue

            first_name = str(rec.get("firstName") or rec.get("first_name") or "").strip()
            last_name  = str(rec.get("lastName")  or rec.get("last_name")  or "").strip()
            if not first_name and not last_name:
                first_name, last_name = _split_name(person)

            tx_date    = str(rec.get("transactionDate") or rec.get("transaction_date") or "")
            file_date  = str(rec.get("dateRecieved")    or rec.get("date_received")
                             or rec.get("disclosureDate") or "")
            order_type = _normalise_type(
                str(rec.get("type") or rec.get("transactionType") or "")
            )
            tx_amount  = str(rec.get("amount") or rec.get("transactionAmount") or "")
            asset_name = str(rec.get("asset") or rec.get("assetDescription") or "")

            all_rows.append({
                "full_name":  person,
                "first_name": first_name,
                "last_name":  last_name,
                "tx_date":    tx_date[:10],   # trim to YYYY-MM-DD
                "file_date":  file_date[:10],
                "order_type": order_type,
                "ticker":     ticker,
                "asset_name": asset_name,
                "tx_amount":  tx_amount,
                "chamber":    chamber,
            })

        LOGGER.info("  Page %d: %d records (total so far: %d)", page, len(batch), len(all_rows))
        page += 1

        # If batch is less than the page size we've reached the last page
        if len(batch) < 250:
            break

    df = pd.DataFrame(all_rows)
    LOGGER.info("FMP %s: %d transactions, %d unique members",
                chamber, len(df), df["full_name"].nunique() if not df.empty else 0)
    return df


def fetch_senate(api_key: str | None = None) -> pd.DataFrame:
    key = api_key or _get_api_key()
    return _fetch_all("senate-trading", "Senate", key)


def fetch_house(api_key: str | None = None) -> pd.DataFrame:
    key = api_key or _get_api_key()
    return _fetch_all("house-trading", "House", key)


def main(chamber: Literal["both", "senate", "house"] = "both") -> pd.DataFrame:
    key = _get_api_key()
    frames = []
    if chamber in ("both", "senate"):
        frames.append(fetch_senate(key))
    if chamber in ("both", "house"):
        frames.append(fetch_house(key))
    if not frames:
        return pd.DataFrame(columns=REPORT_COL_NAMES + ["full_name"])
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    df = main()
    print(df.head(5).to_string())
    print(f"\nTotal: {len(df)}")
    if not df.empty:
        print(f"Chambers: {df['chamber'].value_counts().to_dict()}")
        print(f"Members: {df['full_name'].nunique()}")
