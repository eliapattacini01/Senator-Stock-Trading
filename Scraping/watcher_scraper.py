"""
Senate Stock Watcher — supplementary data source (no API key required).

Fetches pre-scraped Senate transaction data from the public GitHub repo:
  https://github.com/timothycarambat/senate-stock-watcher-data

Coverage: 2014–2019 (~8,350 transactions, 66 senators).
Good for filling historical gaps that the EFDS scraper may have missed.

Note: The S3 bucket (senate-stock-watcher-data.s3-us-west-2.amazonaws.com)
is no longer publicly accessible; this uses the GitHub raw URL instead.
"""

import logging
import requests
import pandas as pd

LOGGER = logging.getLogger(__name__)

SENATE_WATCHER_URL = (
    "https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data"
    "/master/aggregate/all_transactions.json"
)

REPORT_COL_NAMES = [
    "tx_date", "file_date", "last_name", "first_name",
    "order_type", "ticker", "asset_name", "tx_amount", "chamber",
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; congress-trades-bot/1.0)"}


def _split_name(full: str) -> tuple[str, str]:
    """'First Last Jr.' → ('First', 'Last Jr.')"""
    parts = full.strip().split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (full, "")


def fetch() -> pd.DataFrame:
    """Download and normalise all Senate Watcher transactions into a DataFrame."""
    LOGGER.info("Fetching Senate Stock Watcher data from GitHub…")
    resp = requests.get(SENATE_WATCHER_URL, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    records = resp.json()

    LOGGER.info("  Got %d records from Senate Watcher", len(records))

    rows = []
    for rec in records:
        senator = str(rec.get("senator", "")).strip()
        if not senator:
            continue

        first, last = _split_name(senator)
        ticker = str(rec.get("ticker", "")).strip().upper()

        # Skip entries that are PDF placeholders (no ticker)
        if not ticker or ticker in ("--", "N/A", ""):
            continue

        # Some records wrap a list of transactions (newer format)
        txs = rec.get("transactions")
        if txs:
            for tx in txs:
                t = str(tx.get("ticker", "")).strip().upper()
                if not t or t in ("--", ""):
                    continue
                rows.append({
                    "full_name":  senator,
                    "first_name": first,
                    "last_name":  last,
                    "tx_date":    tx.get("transaction_date", ""),
                    "file_date":  rec.get("date_received", rec.get("disclosure_date", "")),
                    "order_type": tx.get("type", ""),
                    "ticker":     t,
                    "asset_name": tx.get("asset_description", ""),
                    "tx_amount":  tx.get("amount", ""),
                    "chamber":    "Senate",
                })
        else:
            # Flat format (single record = single trade)
            rows.append({
                "full_name":  senator,
                "first_name": first,
                "last_name":  last,
                "tx_date":    rec.get("transaction_date", ""),
                "file_date":  rec.get("date_received", rec.get("disclosure_date", "")),
                "order_type": rec.get("type", ""),
                "ticker":     ticker,
                "asset_name": rec.get("asset_description", ""),
                "tx_amount":  rec.get("amount", ""),
                "chamber":    "Senate",
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=REPORT_COL_NAMES + ["full_name"])

    LOGGER.info("  Parsed %d transactions from %d unique senators",
                len(df), df["full_name"].nunique())
    return df


def main() -> pd.DataFrame:
    return fetch()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    df = main()
    print(df.head(5).to_string())
    print(f"\nTotal: {len(df)} | Senators: {df['full_name'].nunique()}")
    print(f"Date range: {df['tx_date'].min()} → {df['tx_date'].max()}")
