"""
Portfolio-performance simulation — mirrors the notebook's return_for_senator() logic.

Prices are read ONLY from the PostgreSQL `prices` table (adjusted daily closes),
which crons/refresh_prices.py keeps up to date. Nothing in this module downloads
data: serving an HTTP request must never call Yahoo / Stooq.

Loaded series are kept in a per-process cache (PRICE_CACHE_TTL seconds, at most
PRICE_CACHE_MAX_TICKERS tickers), and all tickers a simulation needs are fetched
with a single query.

Algorithm:
  - Walk transactions in chronological order
  - Track fractional units held per ticker
  - Time-Weighted Return: daily_return = V(holdings_prev, prices_today) / V(holdings_prev, prices_yesterday)
  - Compare against SPY normalized over the same date range
"""

import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from backend.db import get_connection

LOGGER = logging.getLogger(__name__)

PRICE_CACHE_TTL         = int(os.getenv("PRICE_CACHE_TTL", 6 * 3600))  # seconds
PRICE_CACHE_MAX_TICKERS = int(os.getenv("PRICE_CACHE_MAX_TICKERS", 1000))  # ~30 KB per ticker
_QUERY_CHUNK = 200  # tickers per query, bounds the rows held in memory at once

# STOCK-Act dollar buckets (for "Full sale" detection)
_BUCKETS = [
    (1_000, 15_000), (15_000, 50_000), (50_000, 100_000),
    (100_000, 250_000), (250_000, 500_000), (500_000, 1_000_000),
    (1_000_000, 5_000_000), (5_000_000, 25_000_000),
    (25_000_000, 50_000_000), (50_000_000, float("inf")),
]


# ── price data (read-only) ─────────────────────────────────────────────────────

def ensure_prices_table() -> None:
    """Create the prices table in PostgreSQL if it doesn't exist yet."""
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS prices (
                    ticker VARCHAR(20)       NOT NULL,
                    date   DATE              NOT NULL,
                    price  DOUBLE PRECISION  NOT NULL,
                    PRIMARY KEY (ticker, date)
                )
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        LOGGER.warning("Could not ensure prices table: %s", exc)


# ticker -> (loaded_at, Series(price, index=date) or None when the DB has no rows)
_cache: "OrderedDict[str, tuple[float, Optional[pd.Series]]]" = OrderedDict()
_cache_lock = threading.Lock()


def _query_prices(tickers: list[str]) -> dict[str, pd.Series]:
    """
    Load the full stored history of `tickers` from the prices table.

    One row per ticker with the dates (as days since 1970-01-01) and prices
    aggregated into arrays, fetched in binary: ~4x less data from Neon and
    ~4x faster than one row per (ticker, date).
    """
    out: dict[str, pd.Series] = {}
    conn = get_connection()
    try:
        cur = conn.cursor(binary=True)
        for i in range(0, len(tickers), _QUERY_CHUNK):
            cur.execute("""
                SELECT ticker,
                       array_agg(date - DATE '1970-01-01' ORDER BY date),
                       array_agg(price ORDER BY date)
                FROM prices
                WHERE ticker = ANY(%s)
                GROUP BY ticker
            """, (tickers[i:i + _QUERY_CHUNK],))
            for ticker, days, prices in cur.fetchall():
                index = pd.to_datetime(np.asarray(days, dtype="int64"), unit="D")
                out[ticker] = pd.Series(np.asarray(prices, dtype=float), index=index, name="price")
    finally:
        conn.close()
    return out


def get_price_series(tickers: Iterable[str]) -> dict[str, pd.Series]:
    """
    Return {ticker: Series(price, index=date)} for the requested tickers that have
    stored prices. Cache misses are loaded from the DB in one go.
    """
    wanted = list(dict.fromkeys(t for t in tickers if t))
    now    = time.time()
    found: dict[str, pd.Series] = {}
    missing: list[str] = []

    with _cache_lock:
        for t in wanted:
            hit = _cache.get(t)
            if hit is not None and now - hit[0] < PRICE_CACHE_TTL:
                _cache.move_to_end(t)
                if hit[1] is not None:
                    found[t] = hit[1]
            else:
                missing.append(t)

    if missing:
        loaded = _query_prices(missing)
        with _cache_lock:
            for t in missing:
                series = loaded.get(t)
                _cache[t] = (now, series)
                _cache.move_to_end(t)
                if series is not None:
                    found[t] = series
            while len(_cache) > PRICE_CACHE_MAX_TICKERS:
                _cache.popitem(last=False)

    return found


def _on_or_after(series: Optional[pd.Series], date) -> Optional[float]:
    """First available close price on or after `date`."""
    if series is None:
        return None
    i = series.index.searchsorted(pd.to_datetime(date), side="left")
    return float(series.iloc[i]) if i < len(series) else None


def _on_or_before(series: Optional[pd.Series], date) -> Optional[float]:
    """Last available close price on or before `date`."""
    if series is None:
        return None
    i = series.index.searchsorted(pd.to_datetime(date), side="right") - 1
    return float(series.iloc[i]) if i >= 0 else None


def price_on_or_after(ticker: str, date) -> Optional[float]:
    """First available close price on or after `date`. Returns None if unavailable."""
    return _on_or_after(get_price_series([ticker]).get(ticker), date)


def price_on_or_before(ticker: str, date) -> Optional[float]:
    """Last available close price on or before `date`."""
    return _on_or_before(get_price_series([ticker]).get(ticker), date)


# ── portfolio helpers ──────────────────────────────────────────────────────────

def _same_bucket(a: float, b: float) -> bool:
    for lo, hi in _BUCKETS:
        if lo <= a < hi:
            return lo <= b < hi
    return False


def _portfolio_value(holdings: dict, date, prices: dict) -> float:
    total = 0.0
    for ticker, units in holdings.items():
        if units <= 0:
            continue
        p = _on_or_after(prices.get(ticker), date)
        if p and p > 0:
            total += p * units
    return total


def _tickers_in(rows: pd.DataFrame) -> set:
    return {str(t).strip().upper() for t in rows.get("ticker", [])} | {"SPY"}


# ── main simulation ────────────────────────────────────────────────────────────

def simulate_portfolio(rows: pd.DataFrame) -> dict:
    """
    Simulate a member's portfolio and return growth time series vs SPY.

    Expected columns: tx_date, side (BUY/SELL), ticker, tx_estimate
    Also accepts order_type (Purchase / Sale ...) in place of side.
    """
    from collections import defaultdict

    if rows is None or len(rows) == 0:
        return _empty()

    rows = rows.copy()
    rows["tx_date"] = pd.to_datetime(rows["tx_date"], errors="coerce")
    rows = rows.dropna(subset=["tx_date"]).sort_values("tx_date")

    # Normalise order type → "BUY" / "SELL"
    def _side(row):
        v = str(row.get("side", row.get("order_type", ""))).strip()
        if v.upper() == "BUY" or "purchase" in v.lower():
            return "BUY"
        if v.upper() == "SELL" or "sale" in v.lower():
            return "SELL"
        return ""

    rows["_side"] = rows.apply(_side, axis=1)
    prices = get_price_series(_tickers_in(rows))

    holdings: dict = defaultdict(float)
    before_vals, after_vals, tx_dates = [], [], []

    for _, row in rows.iterrows():
        date    = row["tx_date"]
        ticker  = str(row.get("ticker", "")).strip().upper()
        side    = row["_side"]
        tx_est  = float(row.get("tx_estimate", 0) or 0)

        if not ticker or side not in ("BUY", "SELL"):
            continue

        price = _on_or_after(prices.get(ticker), date)
        if price is None or price <= 0:
            continue

        val_before = _portfolio_value(holdings, date, prices)

        if side == "BUY":
            if tx_est > 0:
                holdings[ticker] += tx_est / price
        else:  # SELL
            held = float(holdings.get(ticker, 0))
            if held > 0:
                cur_val = price * held
                order_raw = str(row.get("order_type", "")).lower()
                if "full" in order_raw or _same_bucket(tx_est, cur_val):
                    holdings[ticker] = 0.0
                else:
                    holdings[ticker] = max(0.0, held - tx_est / price)

        val_after = _portfolio_value(holdings, date, prices)
        before_vals.append(val_before)
        after_vals.append(val_after)
        tx_dates.append(date)

    if len(before_vals) < 2:
        return _empty()

    # Cumulative growth (notebook formula: skip first before-val & last after-val)
    bv = np.array(before_vals[1:],  dtype=float)
    av = np.array(after_vals[:-1],  dtype=float)
    td = tx_dates[1:]

    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.where(av > 0, bv / av, 1.0)
    g = np.where(np.isfinite(g), g, 1.0)

    cumulative = np.cumprod(g)
    start_date = tx_dates[0]
    end_date   = tx_dates[-1]

    years        = max((end_date - start_date).days / 365, 0.01)
    total_return = float(cumulative[-1])
    cagr         = float(total_return ** (1 / years))

    # SPY comparison — normalize to 1.0 at start_date
    spy_growth, spy_total_return = _spy_series(prices.get("SPY"), td, start_date, end_date)

    return {
        "dates":            [d.strftime("%Y-%m-%d") for d in td],
        "portfolio_growth": [round(v, 6) for v in cumulative.tolist()],
        "spy_growth":       spy_growth,
        "total_return":     round(total_return, 4),
        "cagr":             round(cagr, 4),
        "spy_total_return": round(spy_total_return, 4),
        "spy_cagr":         round(float(spy_total_return ** (1 / years)), 4),
        "start_date":       start_date.strftime("%Y-%m-%d"),
        "end_date":         end_date.strftime("%Y-%m-%d"),
        "n_transactions":   len(td),
    }


def simulate_portfolio_daily(rows: pd.DataFrame) -> dict:
    """
    Like simulate_portfolio but returns a dense DAILY growth series.
    Tracks holdings state after each transaction, then for every business
    day computes portfolio value = sum(units * daily_price) using stored prices.
    """
    import bisect
    from collections import defaultdict

    if rows is None or len(rows) == 0:
        return _empty()

    rows = rows.copy()
    rows["tx_date"] = pd.to_datetime(rows["tx_date"], errors="coerce")
    rows = rows.dropna(subset=["tx_date"]).sort_values("tx_date")

    def _side(row):
        v = str(row.get("side", row.get("order_type", ""))).strip()
        if v.upper() == "BUY" or "purchase" in v.lower():
            return "BUY"
        if v.upper() == "SELL" or "sale" in v.lower():
            return "SELL"
        return ""

    rows["_side"] = rows.apply(_side, axis=1)
    prices = get_price_series(_tickers_in(rows))

    # ── replay transactions; capture holdings snapshot after each tx ────────
    holdings: dict = defaultdict(float)
    snapshots = []  # [(pd.Timestamp, {ticker: units})]

    for _, row in rows.iterrows():
        date   = row["tx_date"]
        ticker = str(row.get("ticker", "")).strip().upper()
        side   = row["_side"]
        tx_est = float(row.get("tx_estimate", 0) or 0)

        if not ticker or side not in ("BUY", "SELL"):
            continue

        price = _on_or_after(prices.get(ticker), date)
        if price is None or price <= 0:
            continue

        if side == "BUY":
            if tx_est > 0:
                holdings[ticker] += tx_est / price
        else:
            held = float(holdings.get(ticker, 0))
            if held > 0:
                cur_val   = price * held
                order_raw = str(row.get("order_type", "")).lower()
                if "full" in order_raw or _same_bucket(tx_est, cur_val):
                    holdings[ticker] = 0.0
                else:
                    holdings[ticker] = max(0.0, held - tx_est / price)

        snapshots.append((date, dict(holdings)))

    if not snapshots:
        return _empty()

    # ── build daily date range ───────────────────────────────────────────────
    start_date = snapshots[0][0]
    end_date   = pd.Timestamp.today().normalize()
    daily_dates = pd.bdate_range(start_date, end_date)

    if len(daily_dates) == 0:
        return _empty()

    snap_ts  = [s[0] for s in snapshots]
    snap_hld = [s[1] for s in snapshots]

    # ── collect unique tickers; build forward-filled daily price series ──────
    unique_tickers = set()
    for h in snap_hld:
        unique_tickers.update(h.keys())

    price_series_map: dict = {}
    for ticker in unique_tickers:
        s = prices.get(ticker)
        if s is not None:
            price_series_map[ticker] = s.reindex(daily_dates, method="ffill")

    # ── pre-extract price arrays for fast indexing ───────────────────────────
    price_arrays: dict = {}
    for ticker, series in price_series_map.items():
        price_arrays[ticker] = series.to_numpy(dtype=float)

    n = len(daily_dates)

    # ── Time-Weighted Return (TWR) ────────────────────────────────────────────
    # Each day's return = price change of YESTERDAY's holdings only.
    # New purchases/sales on day T don't affect day T's return — they only
    # start contributing from day T+1 onward.  This prevents big cash injections
    # from showing up as enormous fake "returns".
    #
    # daily_return[i] = V(holdings_{i-1}, prices_i) / V(holdings_{i-1}, prices_{i-1})
    #
    # Cumulative growth = cumprod(daily_returns) starting at 1.0.

    cumulative   = 1.0
    growth_list  = []
    valid_dates  = []
    started      = False          # becomes True once the first holding is priced

    for i in range(n):
        day = daily_dates[i]

        # Holdings as of end of PREVIOUS day (or empty before first transaction)
        prev_snap_idx = bisect.bisect_right(snap_ts, daily_dates[i - 1]) - 1 if i > 0 else -1
        h_prev = snap_hld[prev_snap_idx] if prev_snap_idx >= 0 else {}

        if i == 0:
            # Day 0: just record baseline = 1.0 if any holdings exist after today
            snap_idx = bisect.bisect_right(snap_ts, day) - 1
            if snap_idx >= 0:
                started = True
                growth_list.append(1.0)
                valid_dates.append(day)
            continue

        if not started:
            # Check whether today's snapshot has any holdings
            snap_idx = bisect.bisect_right(snap_ts, day) - 1
            if snap_idx >= 0:
                started = True
                growth_list.append(1.0)
                valid_dates.append(day)
            continue

        # Compute V(h_prev, prices_today) and V(h_prev, prices_yesterday)
        v_today = 0.0
        v_prev  = 0.0
        for ticker, units in h_prev.items():
            if units <= 0 or ticker not in price_arrays:
                continue
            arr = price_arrays[ticker]
            p_t = arr[i]     if i     < len(arr) else np.nan
            p_p = arr[i - 1] if i - 1 < len(arr) else np.nan
            if np.isfinite(p_t) and np.isfinite(p_p) and p_p > 0:
                v_today += units * p_t
                v_prev  += units * p_p

        if v_prev > 0:
            cumulative *= v_today / v_prev

        growth_list.append(cumulative)
        valid_dates.append(day)

    if not growth_list:
        return _empty()

    daily_dates = pd.DatetimeIndex(valid_dates)
    growth      = np.array(growth_list, dtype=float)

    # ── SPY daily series ─────────────────────────────────────────────────────
    spy = prices.get("SPY")
    spy_growth = []
    spy_total  = 1.0
    if spy is not None:
        spy_daily = spy.reindex(daily_dates, method="ffill")
        spy_base  = spy_daily.iloc[0]
        if pd.notna(spy_base) and spy_base > 0:
            spy_series_norm = (spy_daily / spy_base).fillna(1.0)
            spy_growth = [round(float(v), 6) for v in spy_series_norm]
            spy_total  = float(spy_series_norm.iloc[-1])

    total_return = float(growth[-1])
    years = max((daily_dates[-1] - daily_dates[0]).days / 365, 0.01)
    cagr  = float(total_return ** (1 / years))

    return {
        "dates":            [d.strftime("%Y-%m-%d") for d in daily_dates],
        "portfolio_growth": [round(float(v), 6) for v in growth],
        "spy_growth":       spy_growth or [1.0] * len(daily_dates),
        "total_return":     round(total_return, 4),
        "cagr":             round(cagr, 4),
        "spy_total_return": round(float(spy_total), 4),
        "spy_cagr":         round(float(spy_total ** (1 / years)), 4),
        "start_date":       daily_dates[0].strftime("%Y-%m-%d"),
        "end_date":         daily_dates[-1].strftime("%Y-%m-%d"),
        "n_transactions":   len(snapshots),
    }


def _spy_series(spy: Optional[pd.Series], tx_dates: list, start_date, end_date) -> tuple:
    """Return (spy_growth_list, spy_total_return) aligned to tx_dates."""
    if spy is None or len(tx_dates) == 0:
        return [], 1.0

    spy_sub = spy[
        (spy.index >= pd.to_datetime(start_date)) &
        (spy.index <= pd.to_datetime(end_date))
    ]

    if len(spy_sub) < 2:
        return [], 1.0

    base_price = float(spy_sub.iloc[0])
    if base_price <= 0:
        return [], 1.0

    # For each transaction date, find SPY price (last available on or before)
    interp = []
    for d in tx_dates:
        p = _on_or_before(spy_sub, d)
        interp.append((p if p is not None else base_price) / base_price)

    spy_total = float(spy_sub.iloc[-1]) / base_price
    return [round(v, 6) for v in interp], round(spy_total, 4)


def _empty() -> dict:
    return {
        "dates": [], "portfolio_growth": [], "spy_growth": [],
        "total_return": 1.0, "cagr": 1.0,
        "spy_total_return": 1.0, "spy_cagr": 1.0,
        "start_date": None, "end_date": None,
        "n_transactions": 0,
    }
