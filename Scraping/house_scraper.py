"""
Scrape stock transactions from House of Representatives PTR filings.

Data source:
  - Filing list: POST https://disclosures-clerk.house.gov/FinancialDisclosure/ViewMemberSearchResult
  - Filing PDF:  https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf
                 https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}/{doc_id}.pdf
"""

import io
import logging
import re
import time
from typing import List, Optional

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

SEARCH_URL   = "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewMemberSearchResult"
DISC_BASE    = "https://disclosures-clerk.house.gov"

RATE_LIMIT_SECS = 1.5

REPORT_COL_NAMES = [
    "tx_date", "file_date", "last_name", "first_name",
    "order_type", "ticker", "asset_name", "tx_amount", "chamber",
]

_TYPE_MAP = {
    "p":          "Purchase",
    "purchase":   "Purchase",
    "s":          "Sale (Full)",
    "sale":       "Sale (Full)",
    "s (full)":   "Sale (Full)",
    "s (partial)": "Sale (Partial)",
    "sale (partial)": "Sale (Partial)",
    "e":          "Exchange",
    "exchange":   "Exchange",
}

LOGGER = logging.getLogger(__name__)

# Regex to extract ticker symbol from asset description e.g. "(AAPL)" or "(GOOGL) [ST]"
_TICKER_RE = re.compile(r'\(([A-Z]{1,6})\)\s*(?:\[[A-Z]+\])?')


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    })
    return s


def _fetch_filing_list(session: requests.Session) -> List[dict]:
    """
    POST the search form to get all PTR filings (all years, all members).
    Returns list of {first_name, last_name, file_date, pdf_url}.
    """
    LOGGER.info("Fetching House PTR filing list from disclosures-clerk.house.gov…")
    time.sleep(RATE_LIMIT_SECS)
    r = session.post(
        SEARCH_URL,
        data={"lastName": "", "firstName": "", "searchYear": "", "filingType": "P"},
        timeout=30,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    filings = []
    for row in soup.select("tbody tr"):
        link = row.find("a")
        if not link:
            continue
        href = link.get("href", "")
        raw_name = link.get_text(" ", strip=True)  # "Last, First" or "Last, Hon. First"
        raw_name = re.sub(r'^[^a-zA-Z]+', '', raw_name)  # strip leading dots/spaces
        if not raw_name:
            continue
        filing_year = (row.find("td", {"data-label": "Filing Year"}) or {}).get_text(strip=True) if hasattr(row.find("td", {"data-label": "Filing Year"}), 'get_text') else ""

        # Parse "Last, First" name format
        if "," in raw_name:
            last, _, rest = raw_name.partition(",")
            # Strip titles like "Hon."
            first = re.sub(r'\bHon\.?\b', '', rest).strip()
            last = last.strip()
        else:
            parts = raw_name.strip().split()
            last = parts[-1] if parts else raw_name
            first = " ".join(parts[:-1]) if len(parts) > 1 else ""

        pdf_url = f"{DISC_BASE}/{href}" if not href.startswith("http") else href

        filings.append({
            "first_name": first,
            "last_name": last,
            "file_year": filing_year,
            "pdf_url": pdf_url,
        })

    LOGGER.info("  Found %d PTR filings in index", len(filings))
    return filings


def _normalise_type(raw: str) -> Optional[str]:
    return _TYPE_MAP.get(raw.strip().lower())


def _parse_ptr_pdf(pdf_bytes: bytes, first: str, last: str, file_year: str) -> List[list]:
    """
    Parse a House PTR PDF and return rows as lists matching REPORT_COL_NAMES.
    Handles two table formats:
      - Split rows: ['', owner, asset, type, tx_date, notif_date, amount, gains]
      - Merged rows: ['owner asset type tx_date notif_date amount', None, ...]
    """
    rows = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    # Check header row
                    header = [str(c or "").lower().strip() for c in table[0]]
                    if not any("asset" in h or "transaction" in h for h in header):
                        continue

                    for raw_row in table[1:]:
                        cells = [str(c or "").strip() for c in raw_row]

                        # Skip continuation/detail rows (filing status lines)
                        if cells[0].startswith("F") and "Status" in cells[0]:
                            continue

                        # ── Format A: properly split columns ──────────────────
                        # ['', 'SP', 'Asset Name (TICKER) [ST]', 'P', 'date', 'date', 'amount', '']
                        if len(cells) >= 7 and cells[2] and cells[3] and cells[4]:
                            asset_raw = cells[2]
                            type_raw  = cells[3]
                            tx_date   = cells[4]
                            notif_date = cells[5] if len(cells) > 5 else ""
                            amount    = cells[6] if len(cells) > 6 else ""

                            # Skip sub-rows (description/filing-status continuation)
                            if not re.search(r'\d{2}/\d{2}/\d{4}', tx_date):
                                continue

                            ticker_match = _TICKER_RE.search(asset_raw)
                            if not ticker_match:
                                continue
                            ticker = ticker_match.group(1)
                            order_type = _normalise_type(type_raw)
                            if not order_type:
                                continue

                            asset_name = asset_raw.split("\n")[0][:300]
                            rows.append([tx_date, notif_date, last, first,
                                         order_type, ticker, asset_name, amount, "House"])

                        # ── Format B: everything in first cell ────────────────
                        elif cells[0] and all(c in ("", None) for c in cells[1:]):
                            text = cells[0]
                            ticker_match = _TICKER_RE.search(text)
                            if not ticker_match:
                                continue
                            ticker = ticker_match.group(1)

                            date_matches = re.findall(r'\d{2}/\d{2}/\d{4}', text)
                            if len(date_matches) < 1:
                                continue
                            tx_date    = date_matches[0]
                            notif_date = date_matches[1] if len(date_matches) > 1 else tx_date

                            type_match = re.search(
                                r'\b(P|S \(partial\)|S \(full\)|S|Purchase|Sale)\b',
                                text, re.IGNORECASE
                            )
                            order_type = _normalise_type(type_match.group(1)) if type_match else None
                            if not order_type:
                                continue

                            amount_match = re.search(r'\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+', text)
                            amount = amount_match.group(0) if amount_match else ""

                            asset_name = text.split("\n")[0][:300]
                            rows.append([tx_date, notif_date, last, first,
                                         order_type, ticker, asset_name, amount, "House"])

    except Exception as exc:
        LOGGER.debug("PDF parse error for %s %s: %s", first, last, exc)

    return rows


def main(from_year: int = 2020) -> pd.DataFrame:
    """
    Fetch House PTR transactions.
    from_year: only process filings from this year onward (to limit run time).
               Set to 2012 for full history (takes many hours).
    """
    LOGGER.info("Starting House PTR scrape via disclosures-clerk.house.gov")
    session = _make_session()

    filings = _fetch_filing_list(session)

    # Filter by year — some entries have "YYYY - YYYY" ranges, extract the first year
    def _year_int(y: str) -> int:
        m = re.search(r'\d{4}', str(y))
        return int(m.group(0)) if m else 0

    filings = [f for f in filings if _year_int(f["file_year"]) >= from_year]
    LOGGER.info("  Processing %d PTR filings from %d onward", len(filings), from_year)

    all_rows: List[list] = []
    n_ok = 0

    for i, filing in enumerate(filings):
        url = filing["pdf_url"]
        first = filing["first_name"]
        last  = filing["last_name"]
        year  = filing["file_year"]

        try:
            time.sleep(RATE_LIMIT_SECS)
            resp = session.get(url, timeout=20)
            if resp.status_code != 200:
                continue
            if "application/pdf" not in resp.headers.get("content-type", ""):
                continue

            rows = _parse_ptr_pdf(resp.content, first, last, year)
            all_rows.extend(rows)
            n_ok += 1

            if n_ok % 50 == 0:
                LOGGER.info("  Processed %d/%d filings, %d transactions so far",
                            n_ok, len(filings), len(all_rows))

        except Exception as exc:
            LOGGER.debug("Failed %s %s (%s): %s", first, last, url, exc)

    LOGGER.info("House scrape done: %d filings processed, %d transactions", n_ok, len(all_rows))

    if not all_rows:
        return pd.DataFrame(columns=REPORT_COL_NAMES)
    return pd.DataFrame(all_rows, columns=REPORT_COL_NAMES)


if __name__ == "__main__":
    import argparse

    log_format = "[%(asctime)s %(levelname)s] %(message)s"
    logging.basicConfig(level=logging.INFO, format=log_format)

    parser = argparse.ArgumentParser()
    parser.add_argument("--from-year", type=int, default=2022,
                        help="Only scrape filings from this year onward (default: 2022)")
    args = parser.parse_args()

    txs = main(from_year=args.from_year)
    LOGGER.info("Got %d House transactions", len(txs))
    if not txs.empty:
        print(txs.head(10).to_string())
        print(f"\nTotal: {len(txs)}")
        print(f"Members: {txs['last_name'].nunique()}")
