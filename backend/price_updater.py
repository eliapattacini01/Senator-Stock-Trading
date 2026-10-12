"""
Incremental daily-price sync: Yahoo Finance (yfinance) → `prices` table.

Only the cron scripts import this module (crons/refresh_prices.py daily,
crons/backfill_prices.py one-off from a local machine); the web app never does,
so no HTTP request can trigger a download.

For every ticker in `transactions` (plus SPY, the benchmark):
  - no stored prices, or a transaction older than the stored history
        → full download from the first transaction date - HEAD_MARGIN_DAYS
  - last stored date before the latest completed trading day
        → download only from (last stored date - OVERLAP_DAYS). The overlapping
          days are compared with the stored ones: if Yahoo rewrote them (split or
          dividend adjustment), that ticker's history is reloaded in full.
  - up to date, or marked unavailable in ticker_status → skipped
Downloads go BATCH_SIZE tickers per yf.download() call. A ticker that keeps
returning no new data (FAIL_THRESHOLD consecutive runs, last price older than
STALE_DAYS) is marked unavailable; rate-limit / network errors never count.

Only completed sessions are stored: the download window ends before today's
date in New York, so an intraday price is never saved as a close.
"""

import ast
import datetime as _dt
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from backend.db import get_connection

LOGGER = logging.getLogger(__name__)

MIGRATION_SQL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "database", "migration_price_store.sql",
)

BENCHMARK          = "SPY"
BATCH_SIZE         = 100
BATCH_PAUSE        = 1.0                 # seconds between yf.download() calls
OVERLAP_DAYS       = 7                   # calendar days re-downloaded and compared
ADJUST_TOLERANCE   = 2e-4                # relative change on the overlap = history re-adjusted
HEAD_MARGIN_DAYS   = 10                  # prices kept before a ticker's first transaction
HEAD_SLACK_DAYS    = 5                   # weekends / holidays before the first stored price
HISTORY_FLOOR      = _dt.date(2012, 1, 1)
FAIL_THRESHOLD     = 5                   # consecutive empty runs before "unavailable" (daily job)
STALE_DAYS         = 14                  # ...and only if the last stored price is this old
MAX_FAILED_BATCHES = 3                   # consecutive all-transient batches → stop the run

_STATUS_UPSERT = """
    INSERT INTO ticker_status
        (ticker, unavailable, reason, fail_count, last_attempt, last_success, history_from)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker) DO UPDATE SET
        unavailable  = EXCLUDED.unavailable,
        reason       = EXCLUDED.reason,
        fail_count   = EXCLUDED.fail_count,
        last_attempt = EXCLUDED.last_attempt,
        last_success = EXCLUDED.last_success,
        history_from = EXCLUDED.history_from
"""


@dataclass
class _Status:
    unavailable:  bool = False
    reason:       Optional[str] = None
    fail_count:   int = 0
    last_attempt: Optional[_dt.datetime] = None
    last_success: Optional[_dt.datetime] = None
    history_from: Optional[_dt.date] = None


# ── database ───────────────────────────────────────────────────────────────────

def apply_migration(conn) -> None:
    """Run database/migration_price_store.sql (idempotent)."""
    with open(MIGRATION_SQL, encoding="utf-8") as f:
        sql = f.read()
    conn.execute(sql)
    conn.commit()


def _load_state(conn, only: Optional[set]):
    """Return (first_tx, stored, statuses) — see sync_prices()."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, MIN(tx_date) FROM transactions
        WHERE ticker IS NOT NULL AND ticker NOT IN ('--', 'UNKNOWN', '')
          AND tx_date IS NOT NULL
        GROUP BY ticker
    """)
    first_tx = {t: d for t, d in cur.fetchall()}
    if first_tx:
        # The benchmark must cover every member's simulation
        first_tx[BENCHMARK] = min(first_tx.values())

    cur.execute("SELECT ticker, MIN(date), MAX(date) FROM prices GROUP BY ticker")
    stored = {t: (lo, hi) for t, lo, hi in cur.fetchall()}

    statuses: dict = {}
    cur.execute("SELECT to_regclass('public.ticker_status') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("""
            SELECT ticker, unavailable, reason, fail_count, last_attempt, last_success, history_from
            FROM ticker_status
        """)
        statuses = {r[0]: _Status(*r[1:]) for r in cur.fetchall()}

    if only:
        unknown = sorted(only - first_tx.keys())
        if unknown:
            LOGGER.warning("Not in transactions, ignored: %s", ", ".join(unknown))
        first_tx = {t: d for t, d in first_tx.items() if t in only}

    return first_tx, stored, statuses


def _read_overlap(conn, tickers: list, start: _dt.date) -> dict:
    """{ticker: {date: price}} for the stored rows on or after `start`."""
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, date, price FROM prices WHERE ticker = ANY(%s) AND date >= %s",
        (tickers, start),
    )
    out: dict = {}
    for t, d, p in cur.fetchall():
        out.setdefault(t, {})[d] = p
    return out


def _write(conn, rows: list, replace: list, status_rows: list) -> None:
    """
    Upsert `rows` (ticker, date, price) and the ticker_status rows in one transaction.
    Tickers in `replace` were downloaded in full: their stored history is dropped first,
    so no row with an outdated split/dividend adjustment survives.
    """
    with conn.transaction():
        cur = conn.cursor()
        if replace:
            cur.execute("DELETE FROM prices WHERE ticker = ANY(%s)", (replace,))
        if rows:
            cur.execute("""
                CREATE TEMP TABLE _new_prices (
                    ticker VARCHAR(20), date DATE, price DOUBLE PRECISION
                ) ON COMMIT DROP
            """)
            with cur.copy("COPY _new_prices (ticker, date, price) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            cur.execute("""
                INSERT INTO prices (ticker, date, price)
                SELECT ticker, date, price FROM _new_prices
                ON CONFLICT (ticker, date) DO UPDATE SET price = EXCLUDED.price
            """)
        if status_rows:
            cur.executemany(_STATUS_UPSERT, status_rows)
    # transaction() is only a savepoint when a read already opened a transaction
    # (non-autocommit connection): commit explicitly. No-op in autocommit mode.
    conn.commit()


# ── Yahoo Finance ──────────────────────────────────────────────────────────────

def _today_new_york() -> _dt.date:
    return pd.Timestamp.now(tz="America/New_York").date()


class _ErrorCapture(logging.Handler):
    """
    yf.download() reports per-ticker failures only through the "yfinance" logger,
    one record per distinct error: "['TWTR', 'SPLK']: possibly delisted; no timezone found".
    """

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.errors: dict = {}

    def emit(self, record: logging.LogRecord) -> None:
        m = re.match(r"\s*(\[[^\]]*\]):\s*(.*)", record.getMessage(), re.S)
        if not m:
            return
        try:
            symbols = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            return
        for symbol in symbols:
            self.errors[str(symbol)] = m.group(2).strip()


def _download(tickers: list, start: _dt.date, end: _dt.date):
    """
    One yf.download() call for many tickers.
    Returns (closes: DataFrame indexed by date with one column per ticker,
             errors: {ticker: message} reported by yfinance).
    """
    import yfinance as yf

    yf_logger = logging.getLogger("yfinance")
    capture   = _ErrorCapture()
    old_level = yf_logger.level
    if yf_logger.getEffectiveLevel() > logging.ERROR:
        yf_logger.setLevel(logging.ERROR)
    yf_logger.addHandler(capture)
    try:
        data = yf.download(
            tickers, start=start.isoformat(), end=end.isoformat(),
            auto_adjust=True, actions=False, progress=False, threads=True,
            multi_level_index=True,
        )
    except Exception as exc:  # the whole call failed (network, rate limit…)
        return pd.DataFrame(), {t: f"download failed: {exc}" for t in tickers}
    finally:
        yf_logger.removeHandler(capture)
        yf_logger.setLevel(old_level)
    errors = capture.errors

    if data is None or data.empty or "Close" not in data.columns.get_level_values(0):
        return pd.DataFrame(), errors
    close = data["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    return close, errors


def _series(close: pd.DataFrame, ticker: str) -> pd.Series:
    """Valid (positive, finite) closes for one ticker, indexed by Timestamp."""
    if close.empty or ticker not in close.columns:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = close[ticker].dropna().astype(float)
    s = s[np.isfinite(s) & (s > 0)]
    s.index = pd.to_datetime(s.index)
    return s


def _is_transient(error: Optional[str]) -> bool:
    """Rate limits / network errors are transient; "possibly delisted" or no data at all is not."""
    return bool(error) and "possibly delisted" not in error


def _latest_trading_day(end: _dt.date) -> _dt.date:
    close, errors = _download([BENCHMARK], end - _dt.timedelta(days=10), end)
    s = _series(close, BENCHMARK)
    if s.empty:
        raise RuntimeError(
            f"Yahoo Finance returned no data for {BENCHMARK} "
            f"({errors.get(BENCHMARK, 'no error message')}) — unreachable or rate-limited?"
        )
    return s.index.max().date()


# ── sync ───────────────────────────────────────────────────────────────────────

def _batches(jobs: dict):
    """Split {ticker: date} into BATCH_SIZE chunks of similar dates → (tickers, earliest date)."""
    items = sorted(jobs.items(), key=lambda kv: (kv[1], kv[0]))
    for i in range(0, len(items), BATCH_SIZE):
        chunk = items[i:i + BATCH_SIZE]
        yield [t for t, _ in chunk], min(d for _, d in chunk)


def _rows(ticker: str, s: pd.Series) -> list:
    return [(ticker, ts.date(), float(p)) for ts, p in s.items()]


def sync_prices(
    *,
    include_unavailable: bool = False,
    full: bool = False,
    tickers: Optional[list] = None,
    fail_threshold: int = FAIL_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    """
    Bring the prices table up to date. Returns a summary dict.

    include_unavailable  retry tickers marked unavailable (backfill)
    full                 re-download the whole history of every ticker
    tickers              restrict the run to these tickers
    fail_threshold       consecutive empty runs before marking a ticker unavailable
    dry_run              download and compare, but write nothing
    """
    end = _today_new_york()  # exclusive: only completed sessions are stored

    conn = get_connection()
    try:
        if not dry_run:
            apply_migration(conn)
        first_tx, stored, statuses = _load_state(conn, set(tickers) if tickers else None)
    finally:
        conn.close()

    ref = _latest_trading_day(end)
    LOGGER.info("Latest completed trading day: %s", ref)

    # ── plan ────────────────────────────────────────────────────────────────
    full_jobs: dict = {}   # ticker -> download start
    inc_jobs:  dict = {}   # ticker -> last stored date
    stats = dict(tickers=len(first_tx), up_to_date=0, skipped_unavailable=0, full=0,
                 incremental=0, readjusted=0, rows_written=0, misses=0,
                 newly_unavailable=0, transient=0, dry_run=dry_run)

    for t, first in first_tx.items():
        st = statuses.get(t)
        if st and st.unavailable and not include_unavailable:
            stats["skipped_unavailable"] += 1
            continue
        lo, hi = stored.get(t, (None, None))
        covered_from = min(d for d in (lo, st.history_from if st else None) if d) if lo else None
        start = max(first - _dt.timedelta(days=HEAD_MARGIN_DAYS), HISTORY_FLOOR)
        if full or lo is None or first < covered_from - _dt.timedelta(days=HEAD_SLACK_DAYS):
            full_jobs[t] = start
        elif hi < ref:
            inc_jobs[t] = hi
        else:
            stats["up_to_date"] += 1

    LOGGER.info(
        "%d tickers: %d up to date, %d unavailable (skipped), %d incremental, %d full download",
        len(first_tx), stats["up_to_date"], stats["skipped_unavailable"], len(inc_jobs), len(full_jobs),
    )

    now = _dt.datetime.now(_dt.timezone.utc)
    failed_batches = 0

    def outcome(t: str, kind: str, reason: Optional[str] = None, history_from=None) -> tuple:
        """Update the in-memory status of `t` and return its ticker_status row."""
        st = statuses.setdefault(t, _Status())
        st.last_attempt = now
        if kind == "ok":
            st.unavailable, st.reason, st.fail_count, st.last_success = False, None, 0, now
            if history_from:
                st.history_from = history_from
        elif kind == "miss":
            stats["misses"] += 1
            st.fail_count += 1
            st.reason = (reason or "no data returned")[:300]
            last = stored.get(t, (None, None))[1]
            stale = last is None or (ref - last).days >= STALE_DAYS
            if st.fail_count >= fail_threshold and stale and not st.unavailable:
                st.unavailable = True
                stats["newly_unavailable"] += 1
                LOGGER.warning("%s marked unavailable: %s", t, st.reason)
        else:  # transient
            stats["transient"] += 1
        return (t, st.unavailable, st.reason, st.fail_count,
                st.last_attempt, st.last_success, st.history_from)

    def run_batch(batch: list, start: _dt.date, is_full: bool) -> None:
        nonlocal failed_batches
        close, errors = _download(batch, start, end)

        status_rows, rows, replace, n_transient = [], [], [], 0
        conn = get_connection()
        try:
            overlap = {} if is_full else _read_overlap(conn, batch, start)
            for t in batch:
                s = _series(close, t)
                if is_full:
                    s = s[s.index >= pd.Timestamp(full_jobs[t])]
                if s.empty:
                    err = errors.get(t)
                    kind = "transient" if _is_transient(err) else "miss"
                    n_transient += kind == "transient"
                    status_rows.append(outcome(t, kind, err))
                    continue

                if is_full:
                    rows += _rows(t, s)
                    replace.append(t)
                    status_rows.append(outcome(t, "ok", history_from=full_jobs[t]))
                    continue

                # incremental: verify the overlap, then append the new days
                last = stored[t][1]
                old = overlap.get(t, {})
                common = [(ts, p) for ts, p in s.items() if ts.date() in old]
                if not common or any(abs(p / old[ts.date()] - 1) > ADJUST_TOLERANCE for ts, p in common):
                    stats["readjusted"] += 1
                    full_jobs[t] = max(first_tx[t] - _dt.timedelta(days=HEAD_MARGIN_DAYS), HISTORY_FLOOR)
                    continue  # reloaded in full later in this run
                new = s[s.index > pd.Timestamp(last)]
                if new.empty:
                    status_rows.append(outcome(t, "miss", f"no new data since {last}"))
                    continue
                rows += _rows(t, new)
                status_rows.append(outcome(t, "ok"))

            if not dry_run:
                _write(conn, rows, replace, status_rows)
        finally:
            conn.close()

        stats["rows_written"] += len(rows)
        failed_batches = failed_batches + 1 if n_transient == len(batch) else 0
        if failed_batches >= MAX_FAILED_BATCHES:
            raise RuntimeError(
                f"{failed_batches} consecutive batches failed with transient errors "
                f"(e.g. {next(iter(errors.values()), '?')}) — stopping; the next run will resume"
            )

    # Incremental first: tickers whose history was re-adjusted join full_jobs
    batches = list(_batches(inc_jobs))
    for i, (batch, last) in enumerate(batches, 1):
        stats["incremental"] += len(batch)
        run_batch(batch, last - _dt.timedelta(days=OVERLAP_DAYS), is_full=False)
        LOGGER.info("incremental batch %d/%d done (%d rows so far)", i, len(batches), stats["rows_written"])
        time.sleep(BATCH_PAUSE)

    batches = list(_batches(full_jobs))
    for i, (batch, start) in enumerate(batches, 1):
        stats["full"] += len(batch)
        run_batch(batch, start, is_full=True)
        LOGGER.info("full batch %d/%d done (%d rows so far)", i, len(batches), stats["rows_written"])
        time.sleep(BATCH_PAUSE)

    LOGGER.info("Price sync %s: %s", "DRY RUN" if dry_run else "done", stats)
    return stats
