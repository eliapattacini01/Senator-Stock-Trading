"""
Senate stock transaction scraper — efdsearch.senate.gov via Playwright.

Uses a headful (visible) Chromium browser to bypass Akamai bot protection.
Replicates the original flow: accept the prohibition agreement, then POST
to the report data API using the session CSRF token.
"""

import logging
import pickle
import time
from datetime import datetime
from typing import Optional

import pandas as pd
from playwright.sync_api import sync_playwright, Page

LOGGER = logging.getLogger(__name__)

ROOT              = "https://efdsearch.senate.gov"
LANDING_PAGE_URL  = f"{ROOT}/search/home/"
SEARCH_PAGE_URL   = f"{ROOT}/search/"
REPORTS_URL       = f"{ROOT}/search/report/data/"
PDF_PREFIX        = "/search/view/paper/"

BATCH_SIZE      = 100
RATE_LIMIT_SECS = 2

REPORT_COL_NAMES = [
    "tx_date", "file_date", "last_name", "first_name",
    "order_type", "ticker", "asset_name", "tx_amount",
]


def _parse_since(since_date: str) -> datetime:
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(since_date.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised since_date format: {since_date!r}")


def _accept_agreement(page: Page) -> str:
    """
    Navigate to the landing page, accept the prohibition agreement,
    and return the CSRF token from the session cookie.
    """
    LOGGER.info("  Loading eFD landing page…")
    page.goto(LANDING_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    # Extract CSRF token from the form
    csrf = page.input_value("input[name='csrfmiddlewaretoken']")
    LOGGER.info("  Got CSRF token: %s…", csrf[:10])

    # Submit the prohibition agreement form
    page.check("input[name='prohibition_agreement']")
    page.click("button[type='submit'], input[type='submit']")
    time.sleep(2)

    # Get CSRF cookie (may be 'csrftoken' or 'csrf')
    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    token = cookies.get("csrftoken") or cookies.get("csrf") or csrf
    return token


def _reports_api(page: Page, offset: int, token: str, since_date: str) -> list:
    """POST to the reports API and return the data list."""
    payload = {
        "start":                str(offset),
        "length":               str(BATCH_SIZE),
        "report_types":         "[11]",
        "filer_types":          "[]",
        "submitted_start_date": since_date,
        "submitted_end_date":   "",
        "candidate_state":      "",
        "senator_state":        "",
        "office_id":            "",
        "first_name":           "",
        "last_name":            "",
        "csrfmiddlewaretoken":  token,
    }
    # Use page.evaluate to make an authenticated fetch from within the browser session
    result = page.evaluate(
        """
        async ([url, payload]) => {
            const form = new URLSearchParams(payload);
            const resp = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Referer': 'https://efdsearch.senate.gov/search/',
                    'X-CSRFToken': payload.csrfmiddlewaretoken,
                },
                body: form.toString(),
            });
            return await resp.json();
        }
        """,
        [REPORTS_URL, payload],
    )
    return result.get("data", [])


def _txs_for_report(page: Page, row: list) -> pd.DataFrame:
    """Fetch the individual PTR page and parse its transactions."""
    from bs4 import BeautifulSoup

    first, last, _, link_html, date_received = row
    from bs4 import BeautifulSoup as BS
    link = BS(link_html, "lxml").a.get("href")

    if link.startswith(PDF_PREFIX):
        return pd.DataFrame()

    report_url = f"{ROOT}{link}"
    html = page.evaluate(
        """
        async (url) => {
            const resp = await fetch(url);
            return await resp.text();
        }
        """,
        report_url,
    )
    time.sleep(RATE_LIMIT_SECS)

    soup = BeautifulSoup(html, "lxml")
    tbodies = soup.find_all("tbody")
    if not tbodies:
        return pd.DataFrame()

    stocks = []
    for tr in tbodies[0].find_all("tr"):
        cols = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cols) < 8:
            continue
        tx_date, ticker, asset_name, asset_type, order_type, tx_amount = (
            cols[1], cols[3], cols[4], cols[5], cols[6], cols[7]
        )
        if asset_type != "Stock" and ticker.strip() in ("--", ""):
            continue
        stocks.append([tx_date, date_received, last, first, order_type, ticker, asset_name, tx_amount])

    return pd.DataFrame(stocks).rename(columns=dict(enumerate(REPORT_COL_NAMES)))


def main(since_date: str = "01/01/2012 00:00:00") -> pd.DataFrame:
    LOGGER.info("Initializing Playwright browser (since_date=%s)", since_date)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = context.new_page()

        token = _accept_agreement(page)

        # Paginate through all reports
        all_reports = []
        offset = 0
        while True:
            LOGGER.info("  Fetching report list at offset %d…", offset)
            batch = _reports_api(page, offset, token, since_date)
            if not batch:
                break
            all_reports.extend(batch)
            offset += BATCH_SIZE
            time.sleep(RATE_LIMIT_SECS)

        LOGGER.info("  %d PTR filings found", len(all_reports))

        frames = []
        n_rows = 0
        for i, row in enumerate(all_reports):
            if i % 10 == 0:
                LOGGER.info("  Report %d/%d (%d transactions so far)", i, len(all_reports), n_rows)
            txs = _txs_for_report(page, row)
            if not txs.empty:
                frames.append(txs)
                n_rows += len(txs)

        browser.close()

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=REPORT_COL_NAMES)


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
    df = main()
    LOGGER.info("Dumping to .pickle")
    with open("Scraping/notebooks/senators2.pickle", "wb") as f:
        pickle.dump(df, f)
