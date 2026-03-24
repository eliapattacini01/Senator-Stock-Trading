"""
Portfolio-performance simulation — mirrors the notebook's return_for_senator() logic.

Price source priority:
  1. In-memory lru_cache (per process)
  2. PostgreSQL `prices` table  (persists across restarts — works on Render)
  3. Local pickle files in Scraping/notebooks/stocks/  (local dev migration)
  4. Fresh download from Stooq
  5. yfinance fallback (mutual funds, ETFs Stooq doesn't carry)

Algorithm:
  - Walk transactions in chronological order
  - Track fractional units held per ticker
  - Time-Weighted Return: daily_return = V(holdings_prev, prices_today) / V(holdings_prev, prices_yesterday)
  - Compare against SPY normalized over the same date range
"""

import datetime as _dt
import logging
import os
import pickle
import time
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

_ROOT      = os.path.dirname(os.path.dirname(__file__))
STOCKS_DIR = os.path.join(_ROOT, "Scraping", "notebooks", "stocks")

# Re-fetch from source if latest DB price is older than this many days
_PRICE_STALE_DAYS = 2

# STOCK-Act dollar buckets (for "Full sale" detection)
_BUCKETS = [
    (1_000, 15_000), (15_000, 50_000), (50_000, 100_000),
    (100_000, 250_000), (250_000, 500_000), (500_000, 1_000_000),
    (1_000_000, 5_000_000), (5_000_000, 25_000_000),
    (25_000_000, 50_000_000), (50_000_000, float("inf")),
]


# ── price data ─────────────────────────────────────────────────────────────────

def _cache_path(ticker: str) -> str:
    return os.path.join(STOCKS_DIR, f"{ticker}.pickle")


def _df_from_pickle(path: str) -> Optional[pd.DataFrame]:
    try:
        with open(path, "rb") as f:
            raw = pickle.load(f)["price"]
        out = raw[["Date", "Close"]].rename(columns={"Date": "date", "Close": "price"})
        out["date"]  = pd.to_datetime(out["date"], errors="coerce")
        out["price"] = pd.to_numeric(out["price"], errors="coerce")
        return out.dropna(subset=["date", "price"]).sort_values("date").reset_index(drop=True)
    except Exception as exc:
        LOGGER.debug("Cache read failed (%s): %s", path, exc)
        return None


def ensure_prices_table() -> None:
    """Create the prices table in PostgreSQL if it doesn't exist yet."""
    try:
        from backend.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ticker VARCHAR(20)       NOT NULL,
                date   DATE              NOT NULL,
                price  DOUBLE PRECISION  NOT NULL,
                PRIMARY KEY (ticker, date)
            )
        """)
        conn.commit()
        conn.close()
    except Exception as exc:
        LOGGER.warning("Could not ensure prices table: %s", exc)


def _load_from_db(ticker: str) -> Optional[pd.DataFrame]:
    """Load full price history for a ticker from the PostgreSQL prices table."""
    try:
        from backend.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT date, price FROM prices WHERE ticker = %s ORDER BY date",
            (ticker,),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["date", "price"])
        df["date"]  = pd.to_datetime(df["date"])
        df["price"] = df["price"].astype(float)
        return df
    except Exception as exc:
        LOGGER.debug("DB price load failed for %s: %s", ticker, exc)
        return None


def _save_to_db(ticker: str, df: pd.DataFrame) -> None:
    """Upsert a price DataFrame into the PostgreSQL prices table."""
    if df is None or df.empty:
        return
    try:
        from backend.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cutoff = pd.Timestamp(PRICE_HISTORY_START)
        rows = []
        for _, row in df.iterrows():
            d = row["date"]
            if pd.Timestamp(d) < cutoff:
                continue
            d = d.date() if hasattr(d, "date") else d
            p = float(row["price"])
            if p > 0:
                rows.append((ticker, d, p))
        if rows:
            cur.executemany(
                """
                INSERT INTO prices (ticker, date, price)
                VALUES (%s, %s, %s)
                ON CONFLICT (ticker, date) DO UPDATE SET price = EXCLUDED.price
                """,
                rows,
            )
        conn.commit()
        conn.close()
        LOGGER.debug("Saved %d price rows for %s to DB", len(rows), ticker)
    except Exception as exc:
        LOGGER.warning("DB price save failed for %s: %s", ticker, exc)


PRICE_HISTORY_START = "2020-01-01"


def _download_from_stooq(ticker: str) -> Optional[pd.DataFrame]:
    """Download daily prices from Stooq (no local file caching)."""
    url = f"https://stooq.com/q/d/l/?s={ticker}.US&d1={PRICE_HISTORY_START.replace('-', '')}&i=d"
    try:
        df = pd.read_csv(url, timeout=10)
        if df is None or df.empty or "Date" not in df.columns:
            return None
        df["Date"]  = pd.to_datetime(df["Date"],  errors="coerce")
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
        if df.empty:
            return None
        return df[["Date", "Close"]].rename(columns={"Date": "date", "Close": "price"})
    except Exception as exc:
        LOGGER.debug("Stooq download failed for %s: %s", ticker, exc)
        return None


def _download_from_yfinance(ticker: str) -> Optional[pd.DataFrame]:
    """Fallback: download full price history from yfinance (no local file caching)."""
    try:
        import yfinance as yf
        raw = yf.download(ticker, start=PRICE_HISTORY_START, progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        if "Close" not in raw.columns:
            return None
        df = raw[["Close"]].copy()
        df.index = pd.to_datetime(df.index)
        df = df.rename(columns={"Close": "price"})
        df.index.name = "date"
        df = df.reset_index()
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df = df[df["price"] > 0].dropna(subset=["date", "price"])
        df = df.sort_values("date").reset_index(drop=True)
        if df.empty:
            return None
        LOGGER.debug("yfinance fallback succeeded for %s (%d rows)", ticker, len(df))
        return df
    except Exception as exc:
        LOGGER.debug("yfinance fallback failed for %s: %s", ticker, exc)
        return None


@lru_cache(maxsize=1024)
def _price_series(ticker: str) -> Optional[pd.DataFrame]:
    """
    Return sorted (date, price) DataFrame for ticker.

    Priority:
      1. In-memory lru_cache  (free — already returned)
      2. PostgreSQL prices table  (persists on Render — no pickle needed)
      3. Local pickle files  (local dev migration path)
      4. Stooq download
      5. yfinance fallback  (mutual funds, ETFs, non-US tickers)

    Fresh data is always saved back to the DB so the next cold start is instant.
    """
    # ── 1. Try DB ────────────────────────────────────────────────────────────
    df_db = _load_from_db(ticker)
    if df_db is not None and not df_db.empty:
        latest     = df_db["date"].max()
        stale_at   = pd.Timestamp(_dt.date.today() - _dt.timedelta(days=_PRICE_STALE_DAYS))
        if latest >= stale_at:
            return df_db          # fresh enough — done

    # ── 2. Local pickle (migration: load once, save to DB, never needed again) ──
    path = _cache_path(ticker)
    if os.path.exists(path):
        df_pkl = _df_from_pickle(path)
        if df_pkl is not None and not df_pkl.empty:
            _save_to_db(ticker, df_pkl)
            return df_pkl

    # ── 3. Download from Stooq ───────────────────────────────────────────────
    time.sleep(0.12)
    df_fresh = _download_from_stooq(ticker)

    # ── 4. yfinance fallback ─────────────────────────────────────────────────
    if df_fresh is None:
        LOGGER.debug("Stooq returned nothing for %s, trying yfinance…", ticker)
        df_fresh = _download_from_yfinance(ticker)

    if df_fresh is not None:
        _save_to_db(ticker, df_fresh)
        return df_fresh

    # ── 5. Return stale DB data rather than nothing ──────────────────────────
    return df_db


def price_on_or_after(ticker: str, date) -> Optional[float]:
    """First available close price on or after `date`. Returns None if unavailable."""
    df = _price_series(ticker)
    if df is None:
        return None
    date = pd.to_datetime(date)
    rows = df[df["date"] >= date]
    return float(rows["price"].iloc[0]) if not rows.empty else None


def price_on_or_before(ticker: str, date) -> Optional[float]:
    """Last available close price on or before `date`."""
    df = _price_series(ticker)
    if df is None:
        return None
    date = pd.to_datetime(date)
    rows = df[df["date"] <= date]
    return float(rows["price"].iloc[-1]) if not rows.empty else None


# ── portfolio helpers ──────────────────────────────────────────────────────────

def _same_bucket(a: float, b: float) -> bool:
    for lo, hi in _BUCKETS:
        if lo <= a < hi:
            return lo <= b < hi
    return False


def _portfolio_value(holdings: dict, date) -> float:
    total = 0.0
    for ticker, units in holdings.items():
        if units <= 0:
            continue
        p = price_on_or_after(ticker, date)
        if p and p > 0:
            total += p * units
    return total


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

    holdings: dict = defaultdict(float)
    before_vals, after_vals, tx_dates = [], [], []

    for _, row in rows.iterrows():
        date    = row["tx_date"]
        ticker  = str(row.get("ticker", "")).strip().upper()
        side    = row["_side"]
        tx_est  = float(row.get("tx_estimate", 0) or 0)

        if not ticker or side not in ("BUY", "SELL"):
            continue

        price = price_on_or_after(ticker, date)
        if price is None or price <= 0:
            continue

        val_before = _portfolio_value(holdings, date)

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

        val_after = _portfolio_value(holdings, date)
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
    spy_df = _price_series("SPY")
    spy_growth, spy_total_return = _spy_series(spy_df, td, start_date, end_date)

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
    day computes portfolio value = sum(units * daily_price) using Stooq data.
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

        price = price_on_or_after(ticker, date)
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
        ps = _price_series(ticker)
        if ps is not None:
            s = ps.set_index("date")["price"]
            s.index = pd.to_datetime(s.index)
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
    spy_df = _price_series("SPY")
    spy_growth = []
    spy_total  = 1.0
    if spy_df is not None:
        s = spy_df.set_index("date")["price"]
        s.index = pd.to_datetime(s.index)
        spy_daily = s.reindex(daily_dates, method="ffill")
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


def _spy_series(spy_df, tx_dates: list, start_date, end_date) -> tuple:
    """Return (spy_growth_list, spy_total_return) aligned to tx_dates."""
    if spy_df is None or len(tx_dates) == 0:
        return [], 1.0

    spy_sub = spy_df[
        (spy_df["date"] >= pd.to_datetime(start_date)) &
        (spy_df["date"] <= pd.to_datetime(end_date))
    ].sort_values("date")

    if len(spy_sub) < 2:
        return [], 1.0

    base_price = float(spy_sub["price"].iloc[0])
    if base_price <= 0:
        return [], 1.0

    # For each transaction date, find SPY price (last available on or before)
    interp = []
    for d in tx_dates:
        row = spy_sub[spy_sub["date"] <= pd.to_datetime(d)]
        p   = float(row["price"].iloc[-1]) if not row.empty else base_price
        interp.append(p / base_price)

    spy_total = float(spy_sub["price"].iloc[-1]) / base_price
    return [round(v, 6) for v in interp], round(spy_total, 4)


def _empty() -> dict:
    return {
        "dates": [], "portfolio_growth": [], "spy_growth": [],
        "total_return": 1.0, "cagr": 1.0,
        "spy_total_return": 1.0, "spy_cagr": 1.0,
        "start_date": None, "end_date": None,
        "n_transactions": 0,
    }
