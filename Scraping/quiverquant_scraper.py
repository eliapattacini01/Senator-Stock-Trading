"""
QuiverQuant congressional trading scraper.

No API key required for the live endpoint (free tier).
Returns the 1000 most recent congressional trades (both chambers).

Endpoint:
  GET https://api.quiverquant.com/beta/live/congresstrading
"""

import logging
import time

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

URL = "https://api.quiverquant.com/beta/live/congresstrading"

REPORT_COL_NAMES = [
    "tx_date", "file_date", "last_name", "first_name",
    "order_type", "ticker", "asset_name", "tx_amount", "chamber",
]

_CHAMBER_MAP = {
    "representatives": "House",
    "senate":          "Senate",
    "house":           "House",
}

_TYPE_MAP = {
    "purchase": "Purchase",
    "buy":      "Purchase",
    "sale":     "Sale (Full)",
    "sell":     "Sale (Full)",
    "sale (full)":    "Sale (Full)",
    "sale (partial)": "Sale (Partial)",
    "exchange": "Exchange",
}


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", full)


def fetch() -> pd.DataFrame:
    LOGGER.info("Fetching QuiverQuant congressional trades…")
    headers = {"accept": "application/json", "User-Agent": "Mozilla/5.0"}
    time.sleep(1)
    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        LOGGER.warning("Unexpected QuiverQuant response format: %s", type(data))
        return pd.DataFrame(columns=REPORT_COL_NAMES)

    LOGGER.info("  Received %d raw records", len(data))

    rows = []
    for rec in data:
        ticker = str(rec.get("Ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue

        person = str(rec.get("Representative") or "").strip()
        if not person:
            continue
        first, last = _split_name(person)

        chamber_raw = str(rec.get("House") or "").strip().lower()
        chamber = _CHAMBER_MAP.get(chamber_raw, chamber_raw.title())

        tx_date   = str(rec.get("TransactionDate") or "")[:10]
        file_date = str(rec.get("ReportDate")      or "")[:10]
        order_raw = str(rec.get("Transaction") or "").strip()
        order_type = _TYPE_MAP.get(order_raw.lower(), order_raw)
        tx_amount  = str(rec.get("Range") or "")
        asset_name = str(rec.get("Description") or "")

        rows.append({
            "full_name":  person,
            "first_name": first,
            "last_name":  last,
            "tx_date":    tx_date,
            "file_date":  file_date,
            "order_type": order_type,
            "ticker":     ticker,
            "asset_name": asset_name,
            "tx_amount":  tx_amount,
            "chamber":    chamber,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        LOGGER.warning("QuiverQuant returned no usable transactions")
        return pd.DataFrame(columns=REPORT_COL_NAMES + ["full_name"])

    LOGGER.info(
        "QuiverQuant: %d transactions, %d members (%s)",
        len(df),
        df["full_name"].nunique(),
        df["chamber"].value_counts().to_dict(),
    )
    return df


def main() -> pd.DataFrame:
    return fetch()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    df = main()
    print(df.head(10).to_string())
    print(f"\nTotal: {len(df)}")
    if not df.empty:
        print(f"Chambers: {df['chamber'].value_counts().to_dict()}")
        print(f"Members:  {df['full_name'].nunique()}")
