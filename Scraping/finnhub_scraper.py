"""
Finnhub congressional trading scraper.

Free API key: sign up at https://finnhub.io (no credit card needed).
Add to .env:  FINNHUB_API_KEY=your_key_here

Free tier: 30 calls/second, congressional trading included.
Coverage: Both chambers, ongoing updates.

Endpoint:
  GET https://finnhub.io/api/v1/stock/congressional-trading
  Parameters: from (YYYY-MM-DD), to (YYYY-MM-DD), symbol (optional)
"""

import logging
import os
import time
from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/stock/congressional-trading"
RATE_LIMIT  = 0.5   # seconds between calls (free tier allows 30/s but we're polite)

REPORT_COL_NAMES = [
    "tx_date", "file_date", "last_name", "first_name",
    "order_type", "ticker", "asset_name", "tx_amount", "chamber",
]

_TYPE_MAP = {
    "buy":      "Purchase",
    "purchase": "Purchase",
    "sell":     "Sale (Full)",
    "sale":     "Sale (Full)",
}

# Chambers Finnhub may return
_CHAMBER_MAP = {
    "senate":  "Senate",
    "house":   "House",
    "senator": "Senate",
    "rep":     "House",
    "representative": "House",
}


def _get_api_key() -> str:
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FINNHUB_API_KEY not set. Get a free key at https://finnhub.io "
            "and add FINNHUB_API_KEY=your_key to your .env file."
        )
    return key


def _normalise_type(raw: str) -> str:
    return _TYPE_MAP.get(raw.strip().lower(), raw.strip())


def _normalise_chamber(raw: str) -> str:
    return _CHAMBER_MAP.get(raw.strip().lower(), raw.strip().title())


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (full, "")


def fetch(from_date: str = "2012-01-01",
          to_date:   str | None = None,
          api_key:   str | None = None) -> pd.DataFrame:
    """
    Fetch all congressional trades between from_date and to_date.
    Finnhub returns all members (Senate + House) in one endpoint.
    """
    key      = api_key or _get_api_key()
    to_date  = to_date or date.today().isoformat()

    LOGGER.info("Fetching Finnhub congressional trades %s → %s…", from_date, to_date)

    params = {
        "from":  from_date,
        "to":    to_date,
        "token": key,
    }
    time.sleep(RATE_LIMIT)
    resp = requests.get(FINNHUB_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    if "error" in payload:
        raise RuntimeError(f"Finnhub API error: {payload['error']}")

    trades = payload.get("data", [])
    LOGGER.info("  Received %d raw records", len(trades))

    rows = []
    for rec in trades:
        ticker = str(rec.get("symbol") or "").strip().upper()
        if not ticker or ticker in ("--", ""):
            continue

        person = str(rec.get("name") or "").strip()
        if not person:
            continue
        first, last = _split_name(person)

        chamber_raw = str(rec.get("position") or rec.get("chamber") or "")
        chamber     = _normalise_chamber(chamber_raw)

        tx_date    = str(rec.get("transactionDate") or "")[:10]
        file_date  = str(rec.get("filingDate")       or "")[:10]
        order_type = _normalise_type(str(rec.get("transactionType") or ""))
        # amount: Finnhub provides amountFrom / amountTo
        amt_lo = rec.get("amountFrom") or rec.get("amount")
        amt_hi = rec.get("amountTo")
        if amt_lo and amt_hi:
            tx_amount = f"${int(amt_lo):,} - ${int(amt_hi):,}"
        elif amt_lo:
            tx_amount = f"${int(amt_lo):,}"
        else:
            tx_amount = ""

        asset_name = str(rec.get("name") or rec.get("assetDescription") or "")

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
        LOGGER.warning("Finnhub returned no usable transactions")
        return pd.DataFrame(columns=REPORT_COL_NAMES + ["full_name"])

    LOGGER.info("Finnhub: %d transactions, %d members (%s)",
                len(df), df["full_name"].nunique(),
                df["chamber"].value_counts().to_dict())
    return df


def main() -> pd.DataFrame:
    return fetch()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    df = main()
    print(df.head(5).to_string())
    print(f"\nTotal: {len(df)}")
    if not df.empty:
        print(f"Chambers: {df['chamber'].value_counts().to_dict()}")
        print(f"Members:  {df['full_name'].nunique()}")
