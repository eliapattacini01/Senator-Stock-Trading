"""
Cron Job — pre-compute leaderboard and top-stocks for all periods and store
results in PostgreSQL cache tables.

Run this AFTER refresh_prices.py so prices are fresh.
Schedule: daily at 06:30 UTC (after price refresh at 06:00).

Tables created/updated:
  leaderboard_cache  — top members by return per period
  top_stocks_cache   — top bought/sold tickers per period
"""
import sys
import os
import datetime as _dt
import logging
import re as _re

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s %(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

from collections import defaultdict
import pandas as pd

from backend.db import get_connection

# ── helpers (mirrors backend/main.py) ─────────────────────────────────────────

_SUFFIX_RE = _re.compile(
    r'\b(jr\.?|sr\.?|ii|iii|iv|v|hon\.?|dr\.?|mr\.?|mrs\.?|ms\.?|rep\.?|sen\.?)\b',
    _re.IGNORECASE,
)

def _norm(name):
    n = name.lower()
    n = _re.sub(r'[.,\-]', ' ', n)
    n = _SUFFIX_RE.sub(' ', n)
    return _re.sub(r'\s+', ' ', n).strip()

def _norm_key(name):
    tokens = _norm(name).split()
    if not tokens: return (name.lower(), name.lower())
    if len(tokens) == 1: return (tokens[0], tokens[0])
    return (tokens[0], tokens[-1])

def _period_start(period, today):
    if period == "1M":  return today - _dt.timedelta(days=30)
    if period == "3M":  return today - _dt.timedelta(days=90)
    if period == "YTD": return _dt.date(today.year, 1, 1)
    return today - _dt.timedelta(days=365)  # 1Y

PERIODS = ["1M", "3M", "YTD", "1Y"]

# ── ensure cache tables exist ──────────────────────────────────────────────────

def ensure_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard_cache (
            period         VARCHAR(10)       NOT NULL,
            rank           INT               NOT NULL,
            full_name      VARCHAR(200),
            chamber        VARCHAR(20),
            period_return  DOUBLE PRECISION,
            total_invested BIGINT,
            n_trades       INT,
            n_priced       INT,
            computed_at    TIMESTAMP,
            PRIMARY KEY (period, rank)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS top_stocks_cache (
            period       VARCHAR(10) NOT NULL,
            side         VARCHAR(4)  NOT NULL,
            rank         INT         NOT NULL,
            ticker       VARCHAR(20),
            n_trades     INT,
            n_members    INT,
            price_change DOUBLE PRECISION,
            computed_at  TIMESTAMP,
            PRIMARY KEY (period, side, rank)
        )
    """)
    conn.commit()

# ── leaderboard computation ────────────────────────────────────────────────────

def compute_leaderboard(period, today, conn, limit=50):
    start = _period_start(period, today)
    cur = conn.cursor()
    cur.execute("""
        SELECT full_name, chamber, ticker, tx_date, tx_estimate
        FROM transactions
        WHERE side = 'BUY'
          AND tx_date >= %s AND tx_date <= %s
          AND ticker IS NOT NULL AND ticker NOT IN ('--','UNKNOWN','')
          AND tx_estimate > 0
        ORDER BY full_name, tx_date
    """, (start, today))
    rows = cur.fetchall()

    if not rows:
        return []

    member_trades  = defaultdict(list)
    member_chamber = {}
    for name, chamber, ticker, tx_date, tx_estimate in rows:
        member_trades[name].append((ticker, tx_date, float(tx_estimate or 0)))
        member_chamber[name] = chamber

    unique_tickers = {t for trades in member_trades.values() for t, _, _ in trades}
    LOGGER.info("[%s] pricing %d unique tickers…", period, len(unique_tickers))

    # Commit so the connection isn't idle-in-transaction during price loading
    conn.commit()

    # Bulk-load all prices in one query instead of one query per ticker
    price_map = {}
    if unique_tickers:
        cur2 = conn.cursor()
        cur2.execute(
            "SELECT ticker, date, price FROM prices WHERE ticker = ANY(%s) ORDER BY ticker, date",
            (list(unique_tickers),)
        )
        tmp = defaultdict(list)
        for t, d, p in cur2.fetchall():
            tmp[t].append((d, p))
        for t, pairs in tmp.items():
            s = pd.Series(
                [p for _, p in pairs],
                index=pd.to_datetime([d for d, _ in pairs]),
                name="price",
            ).sort_index()
            price_map[t] = s
        LOGGER.info("[%s] loaded prices from DB for %d tickers", period, len(price_map))

    today_ts = pd.Timestamp(today)
    results  = []

    for name, trades in member_trades.items():
        total_weight = weighted_ret = total_invested = 0.0
        n_priced = 0
        for ticker, tx_date, estimate in trades:
            if ticker not in price_map or estimate <= 0:
                continue
            series = price_map[ticker]
            tx_ts  = pd.Timestamp(tx_date)
            try:
                # Find the position asof would use, then check it isn't too stale
                pos = series.index.searchsorted(tx_ts, side="right") - 1
                if pos < 0:
                    continue
                actual_ts = series.index[pos]
                if (tx_ts - actual_ts).days > 7:
                    continue  # Price data too far before transaction — skip to avoid inflated returns
                p_then = series.iloc[pos]
                p_now  = series.asof(today_ts)
            except Exception:
                continue
            if pd.isna(p_then) or p_then <= 0 or pd.isna(p_now) or p_now <= 0:
                continue
            ret = p_now / p_then - 1.0
            weighted_ret   += estimate * ret
            total_weight   += estimate
            total_invested += estimate
            n_priced       += 1

        if total_weight > 0 and n_priced >= 1:
            results.append({
                "full_name":      name,
                "chamber":        member_chamber.get(name, ""),
                "period_return":  round(weighted_ret / total_weight, 4),
                "total_invested": round(total_invested),
                "n_trades":       len(trades),
                "n_priced":       n_priced,
            })

    # Deduplicate by (first, last) token key
    norm_groups = {}
    for r in results:
        key = _norm_key(r["full_name"])
        if key not in norm_groups or abs(r["period_return"]) > abs(norm_groups[key]["period_return"]):
            norm_groups[key] = r

    return sorted(norm_groups.values(), key=lambda x: x["period_return"], reverse=True)[:limit]


def save_leaderboard(period, rows, conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM leaderboard_cache WHERE period = %s", (period,))
    now = _dt.datetime.utcnow()
    for rank, r in enumerate(rows, start=1):
        cur.execute("""
            INSERT INTO leaderboard_cache
                (period, rank, full_name, chamber, period_return, total_invested, n_trades, n_priced, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (period, rank, r["full_name"], r["chamber"], r["period_return"],
              r["total_invested"], r["n_trades"], r["n_priced"], now))
    conn.commit()
    LOGGER.info("[%s] leaderboard saved: %d rows", period, len(rows))


# ── top stocks computation ─────────────────────────────────────────────────────

def compute_top_stocks(period, today, conn, top_n=5):
    start = _period_start(period, today)
    cur   = conn.cursor()
    results = {}

    for side in ("BUY", "SELL"):
        cur.execute("""
            SELECT ticker, COUNT(*) AS n_trades, COUNT(DISTINCT full_name) AS n_members
            FROM transactions
            WHERE side = %s
              AND tx_date >= %s AND tx_date <= %s
              AND ticker IS NOT NULL AND ticker NOT IN ('--','UNKNOWN','')
            GROUP BY ticker
            ORDER BY n_trades DESC, n_members DESC
            LIMIT %s
        """, (side, start, today, top_n))
        results[side] = cur.fetchall()

    # Price change from prices table
    all_tickers = {r[0] for rows in results.values() for r in rows}
    price_change = {}
    for ticker in all_tickers:
        cur.execute("""
            SELECT price FROM prices WHERE ticker = %s AND date >= %s
            ORDER BY date ASC LIMIT 1
        """, (ticker, start))
        row_start = cur.fetchone()
        cur.execute("""
            SELECT price FROM prices WHERE ticker = %s
            ORDER BY date DESC LIMIT 1
        """, (ticker,))
        row_end = cur.fetchone()
        if row_start and row_end and row_start[0] > 0:
            price_change[ticker] = round((row_end[0] - row_start[0]) / row_start[0], 4)

    return results, price_change


def save_top_stocks(period, results, price_change, conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM top_stocks_cache WHERE period = %s", (period,))
    now = _dt.datetime.utcnow()
    for side, rows in results.items():
        for rank, (ticker, n_trades, n_members) in enumerate(rows, start=1):
            cur.execute("""
                INSERT INTO top_stocks_cache
                    (period, side, rank, ticker, n_trades, n_members, price_change, computed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (period, side, rank, ticker, n_trades, n_members, price_change.get(ticker), now))
    conn.commit()
    LOGGER.info("[%s] top stocks saved", period)


# ── main ───────────────────────────────────────────────────────────────────────

today = _dt.date.today()
conn  = get_connection()
ensure_tables(conn)

for period in PERIODS:
    LOGGER.info("Computing leaderboard for %s…", period)
    lb_rows = compute_leaderboard(period, today, conn)
    save_leaderboard(period, lb_rows, conn)

    LOGGER.info("Computing top stocks for %s…", period)
    ts_results, ts_price_change = compute_top_stocks(period, today, conn)
    save_top_stocks(period, ts_results, ts_price_change, conn)

conn.close()
LOGGER.info("All periods done.")
