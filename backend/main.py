import datetime as _dt
import logging
import os
import re as _re
import time
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.db import get_connection

LOGGER = logging.getLogger(__name__)

# ── name normalisation ─────────────────────────────────────────────────────────

_SUFFIX_RE = _re.compile(
    r'\b(jr\.?|sr\.?|ii|iii|iv|v|hon\.?|dr\.?|mr\.?|mrs\.?|ms\.?|rep\.?|sen\.?)\b',
    _re.IGNORECASE,
)


def _norm(name: str) -> str:
    """Return a canonical lowercase string (suffixes + punctuation stripped)."""
    n = name.lower()
    n = _re.sub(r'[.,\-]', ' ', n)
    n = _SUFFIX_RE.sub(' ', n)
    n = _re.sub(r'\s+', ' ', n).strip()
    return n


def _norm_key(name: str) -> tuple:
    """
    Return a (first_token, last_token) key for grouping name variants.

    This handles middle-name / middle-initial differences:
      "Shelley Moore Capito"  → ("shelley", "capito")
      "Shelley M. Capito"     → ("shelley", "capito")  ← same key, will merge
    """
    tokens = _norm(name).split()
    if not tokens:
        return (name.lower(), name.lower())
    if len(tokens) == 1:
        return (tokens[0], tokens[0])
    return (tokens[0], tokens[-1])


def _name_variants(person: str, conn) -> list[str]:
    """Return all DB name variants that share the same (first, last) token key."""
    key = _norm_key(person)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT full_name FROM transactions")
    return [r[0] for r in cur.fetchall() if _norm_key(r[0]) == key] or [person]

# ── price cache (in-process, ~1 h TTL) ────────────────────────────────────────
_price_cache: dict = {}
_price_ts:    dict = {}
PRICE_TTL = 3600  # seconds


def _fetch_prices(tickers: list[str]) -> dict[str, Optional[float]]:
    """Fetch latest close prices from yfinance with a simple in-memory cache."""
    now  = time.time()
    result: dict[str, Optional[float]] = {}
    stale = [t for t in tickers if now - _price_ts.get(t, 0) > PRICE_TTL]
    fresh = [t for t in tickers if t not in stale]

    for t in fresh:
        result[t] = _price_cache.get(t)

    if stale:
        try:
            import yfinance as yf
            if len(stale) == 1:
                data = yf.download(stale[0], period="5d", progress=False)
                col  = "Close"
                if not data.empty and col in data.columns:
                    price = float(data[col].dropna().iloc[-1])
                    _price_cache[stale[0]] = price
                    _price_ts[stale[0]]    = now
                    result[stale[0]]       = price
                else:
                    result[stale[0]] = None
            else:
                data = yf.download(stale, period="5d", progress=False, group_by="ticker")
                for t in stale:
                    try:
                        price = float(data[t]["Close"].dropna().iloc[-1])
                        _price_cache[t] = price
                        _price_ts[t]    = now
                        result[t]       = price
                    except Exception:
                        result[t] = None
        except Exception as exc:
            LOGGER.warning("yfinance error: %s", exc)
            for t in stale:
                result[t] = None

    return result


# ── FastAPI app ────────────────────────────────────────────────────────────────

def _ensure_schema() -> None:
    """Apply any missing columns/tables so the app works on a fresh DB."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          SERIAL PRIMARY KEY,
                full_name   VARCHAR(200),
                ticker      VARCHAR(20),
                side        VARCHAR(10),
                tx_date     DATE,
                file_date   DATE,
                tx_estimate BIGINT,
                asset_name  VARCHAR(500),
                chamber     VARCHAR(20) DEFAULT 'Senate'
            )
        """)
        # Add columns that may be missing on older DB instances
        for col, definition in [
            ("chamber",     "VARCHAR(20) DEFAULT 'Senate'"),
            ("file_date",   "DATE"),
            ("asset_name",  "VARCHAR(500)"),
            ("tx_estimate", "BIGINT"),
        ]:
            cur.execute(f"""
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS {col} {definition}
            """)
        conn.commit()
    except Exception as exc:
        LOGGER.warning("Schema migration error: %s", exc)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure schema is up to date (safe to run on every startup)
    try:
        _ensure_schema()
    except Exception as exc:
        LOGGER.warning("Schema migration failed: %s", exc)

    # Ensure prices table exists in DB
    try:
        from backend.performance import ensure_prices_table
        ensure_prices_table()
    except Exception as exc:
        LOGGER.warning("Could not ensure prices table: %s", exc)

    # Start APScheduler only when NOT running on Render
    # (on Render, Cron Job services handle scheduling instead)
    on_render = bool(os.getenv("RENDER"))
    if not on_render:
        try:
            from backend.scheduler import start as start_scheduler
            start_scheduler()
        except Exception as exc:
            LOGGER.warning("Scheduler not started: %s", exc)
    else:
        LOGGER.info("Running on Render — APScheduler disabled (using Render Cron Jobs)")

    yield

    # Shutdown scheduler if it was started
    if not on_render:
        try:
            from backend.scheduler import stop as stop_scheduler
            stop_scheduler()
        except Exception:
            pass


app = FastAPI(title="Congress Stock Trades API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "https://senator-stock-trading.onrender.com",
        "https://senator-stock-trading-1.onrender.com",
        "https://senator-stock-trading-2.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── allow-lists for query validation ──────────────────────────────────────────
ALLOWED_SORT    = {"tx_date", "tx_estimate", "ticker", "full_name", "side", "chamber"}
ALLOWED_ORDER   = {"asc", "desc"}
ALLOWED_PERIOD  = {"week", "month", "year"}
ALLOWED_SIDE    = {"BUY", "SELL"}
ALLOWED_CHAMBER = {"Senate", "House"}


# ── /transactions ─────────────────────────────────────────────────────────────

@app.get("/transactions")
def get_transactions(
    senator: str | None = None,
    side:    str | None = None,
    ticker:  str | None = None,
    chamber: str | None = None,
    limit:   int = Query(50, ge=1, le=200),
    offset:  int = Query(0, ge=0),
    sort:    str = "tx_date",
    order:   str = "desc",
):
    sort  = sort.lower()
    order = order.lower()

    if sort not in ALLOWED_SORT:
        raise HTTPException(400, f"Invalid sort. Use one of: {sorted(ALLOWED_SORT)}")
    if order not in ALLOWED_ORDER:
        raise HTTPException(400, "Invalid order. Use asc or desc.")
    if chamber and chamber not in ALLOWED_CHAMBER:
        raise HTTPException(400, "chamber must be Senate or House")

    conn = get_connection()
    try:
        cur = conn.cursor()
        sql    = "SELECT full_name, ticker, side, tx_date, tx_estimate, chamber, file_date FROM transactions WHERE 1=1"
        params = []

        if senator:
            variants = _name_variants(senator, conn)
            sql += f" AND full_name = ANY(%s)"; params.append(variants)
        if side:
            sql += " AND side = %s";    params.append(side)
        if ticker:
            sql += " AND ticker = %s";  params.append(ticker)
        if chamber:
            sql += " AND chamber = %s"; params.append(chamber)

        sql += f" ORDER BY {sort} {order} LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(sql, params)
        rows = cur.fetchall()
        return [
            {"full_name": r[0], "ticker": r[1], "side": r[2],
             "tx_date": r[3], "tx_estimate": r[4], "chamber": r[5],
             "file_date": r[6].isoformat() if r[6] else None}
            for r in rows
        ]
    finally:
        conn.close()


# ── /transactions/count ───────────────────────────────────────────────────────

@app.get("/transactions/count")
def count_transactions(
    senator: str | None = None,
    side:    str | None = None,
    ticker:  str | None = None,
    chamber: str | None = None,
):
    conn = get_connection()
    try:
        cur    = conn.cursor()
        sql    = "SELECT COUNT(*) FROM transactions WHERE 1=1"
        params = []

        if senator:
            variants = _name_variants(senator, conn)
            sql += f" AND full_name = ANY(%s)"; params.append(variants)
        if side:
            sql += " AND side = %s";    params.append(side)
        if ticker:
            sql += " AND ticker = %s";  params.append(ticker)
        if chamber:
            sql += " AND chamber = %s"; params.append(chamber)

        cur.execute(sql, params)
        return {"total": cur.fetchone()[0]}
    finally:
        conn.close()


# ── /senators (all members) ───────────────────────────────────────────────────

@app.get("/senators")
def get_senators(limit: int = 500, chamber: str | None = None):
    """Return deduplicated member names."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = """
            SELECT full_name, COUNT(*) AS cnt
            FROM transactions WHERE 1=1
        """
        params = []
        if chamber:
            if chamber not in ALLOWED_CHAMBER:
                raise HTTPException(400, "chamber must be Senate or House")
            sql += " AND chamber = %s"; params.append(chamber)
        sql += " GROUP BY full_name ORDER BY full_name ASC"
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    # Group by (first, last) token key; pick the variant with the highest count
    groups: dict = {}  # norm_key -> (best_name, best_count)
    for name, cnt in rows:
        key = _norm_key(name)
        if key not in groups or cnt > groups[key][1]:
            groups[key] = (name, cnt)

    canonical = sorted(g[0] for g in groups.values())
    return [{"full_name": n} for n in canonical[:limit]]


# ── /tickers ──────────────────────────────────────────────────────────────────

@app.get("/tickers")
def get_tickers(limit: int = 5000):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ticker
            FROM transactions
            WHERE ticker IS NOT NULL AND ticker NOT IN ('--','UNKNOWN','')
            ORDER BY ticker ASC
            LIMIT %s
        """, (limit,))
        return [{"ticker": r[0]} for r in cur.fetchall()]
    finally:
        conn.close()


# ── /stats ────────────────────────────────────────────────────────────────────

@app.get("/stats")
def get_stats():
    """Summary statistics for the dashboard header cards."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*)                                                        AS total_txs,
                COUNT(DISTINCT full_name)                                       AS total_members,
                COUNT(DISTINCT ticker)                                          AS total_tickers,
                COUNT(*) FILTER (WHERE side = 'BUY')                           AS total_buys,
                COUNT(*) FILTER (WHERE side = 'SELL')                          AS total_sells,
                COUNT(DISTINCT full_name) FILTER (WHERE chamber = 'Senate')    AS senate_members,
                COUNT(DISTINCT full_name) FILTER (WHERE chamber = 'House')     AS house_members,
                MAX(tx_date)                                                    AS latest_date
            FROM transactions
        """)
        r = cur.fetchone()
        return {
            "total_transactions":  r[0],
            "total_members":       r[1],
            "total_tickers":       r[2],
            "total_buys":          r[3],
            "total_sells":         r[4],
            "senate_members":      r[5],
            "house_members":       r[6],
            "latest_date":         r[7].isoformat() if r[7] else None,
        }
    finally:
        conn.close()


# ── /activity/top ─────────────────────────────────────────────────────────────

@app.get("/activity/top")
def top_activity(
    period:  str = Query("week"),
    side:    str = Query("BUY"),
    top_n:   int = Query(10, ge=1, le=50),
    chamber: str | None = None,
    start:   str | None = None,
    end:     str | None = None,
):
    period = period.lower()
    side   = side.upper()

    if period not in ALLOWED_PERIOD:
        raise HTTPException(400, f"period must be one of {sorted(ALLOWED_PERIOD)}")
    if side not in ALLOWED_SIDE:
        raise HTTPException(400, "side must be BUY or SELL")
    if chamber and chamber not in ALLOWED_CHAMBER:
        raise HTTPException(400, "chamber must be Senate or House")

    unit = period  # week | month | year – all valid date_trunc units

    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = f"""
            WITH bucketed AS (
                SELECT
                    date_trunc('{unit}', tx_date)::date AS bucket_start,
                    ticker,
                    COUNT(DISTINCT full_name) AS n_senators,
                    COUNT(*)                  AS n_trades,
                    COALESCE(SUM(tx_estimate), 0) AS total_estimate
                FROM transactions
                WHERE side = %s
        """
        params: list = [side]

        if chamber:
            sql += " AND chamber = %s"; params.append(chamber)
        if start:
            sql += " AND tx_date >= %s"; params.append(start)
        if end:
            sql += " AND tx_date <= %s"; params.append(end)

        sql += """
                GROUP BY 1, 2
            ),
            ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY bucket_start
                        ORDER BY n_senators DESC, n_trades DESC, total_estimate DESC, ticker ASC
                    ) AS rnk
                FROM bucketed
            )
            SELECT bucket_start, ticker, n_senators, n_trades, total_estimate
            FROM ranked
            WHERE rnk <= %s
            ORDER BY bucket_start DESC, n_senators DESC, ticker ASC
        """
        params.append(top_n)

        cur.execute(sql, params)
        rows = cur.fetchall()
        return [
            {"bucket_start": r[0].isoformat(), "ticker": r[1],
             "n_senators": r[2], "n_trades": r[3], "total_estimate": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


# ── /timeseries/monthly ───────────────────────────────────────────────────────

@app.get("/timeseries/monthly")
def monthly_timeseries(
    ticker:  str = Query(..., min_length=1),
    mode:    str = Query("both"),
    chamber: str | None = None,
):
    mode = mode.lower()
    if mode not in {"buy", "sell", "both"}:
        raise HTTPException(400, "mode must be buy, sell, or both")
    if chamber and chamber not in ALLOWED_CHAMBER:
        raise HTTPException(400, "chamber must be Senate or House")

    tkr = ticker.strip().upper()

    conn = get_connection()
    try:
        cur    = conn.cursor()
        sql    = """
            SELECT
                date_trunc('month', tx_date)::date AS month_start,
                COUNT(DISTINCT CASE WHEN side = 'BUY'  THEN full_name END) AS buy_senators,
                COUNT(DISTINCT CASE WHEN side = 'SELL' THEN full_name END) AS sell_senators
            FROM transactions
            WHERE ticker = %s
        """
        params = [tkr]
        if chamber:
            sql += " AND chamber = %s"; params.append(chamber)
        sql += " GROUP BY 1 ORDER BY 1"

        cur.execute(sql, params)
        rows = cur.fetchall()

        out = [
            {"month_start": r[0].isoformat(), "buy_senators": r[1] or 0, "sell_senators": r[2] or 0}
            for r in rows
        ]
        if mode == "buy":
            for d in out: d.pop("sell_senators", None)
        elif mode == "sell":
            for d in out: d.pop("buy_senators",  None)

        return out
    finally:
        conn.close()


# ── /portfolio ────────────────────────────────────────────────────────────────

@app.get("/portfolio")
def get_portfolio(
    person: str = Query(..., min_length=1),
    fetch_prices: bool = Query(True),
):
    """
    Portfolio summary for a single member.

    Returns per-ticker aggregates: total estimated buys, sells, trade counts,
    last transaction date. Optionally fetches current market prices via yfinance.
    """
    conn = get_connection()
    try:
        variants = _name_variants(person, conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, side, tx_date, tx_estimate
            FROM transactions
            WHERE full_name = ANY(%s)
            ORDER BY tx_date ASC
        """, (variants,))
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return {"person": person, "positions": [], "total_bought": 0, "total_sold": 0}

    from collections import defaultdict
    pos: dict = defaultdict(lambda: {
        "n_buys": 0, "n_sells": 0,
        "total_bought": 0.0, "total_sold": 0.0,
        "last_tx_date": None, "last_side": None,
    })

    for ticker, side, tx_date, tx_estimate in rows:
        p   = pos[ticker]
        est = float(tx_estimate or 0)
        if side == "BUY":
            p["n_buys"]      += 1
            p["total_bought"] += est
        else:
            p["n_sells"]     += 1
            p["total_sold"]  += est
        if p["last_tx_date"] is None or tx_date > p["last_tx_date"]:
            p["last_tx_date"] = tx_date
            p["last_side"]    = side

    # Build position list
    positions = []
    for ticker, p in pos.items():
        net = p["total_bought"] - p["total_sold"]
        positions.append({
            "ticker":        ticker,
            "n_buys":        p["n_buys"],
            "n_sells":       p["n_sells"],
            "total_bought":  round(p["total_bought"], 2),
            "total_sold":    round(p["total_sold"],   2),
            "net_invested":  round(net, 2),
            "direction":     "LONG" if net >= 0 else "SHORT",
            "last_tx_date":  p["last_tx_date"].isoformat() if p["last_tx_date"] else None,
            "last_side":     p["last_side"],
            "current_price": None,
        })

    positions.sort(key=lambda x: x["total_bought"], reverse=True)

    # Optionally fetch live prices
    if fetch_prices:
        tickers_to_price = [p["ticker"] for p in positions]
        prices = _fetch_prices(tickers_to_price)
        for p in positions:
            p["current_price"] = prices.get(p["ticker"])

    total_bought = round(sum(p["total_bought"] for p in positions), 2)
    total_sold   = round(sum(p["total_sold"]   for p in positions), 2)

    return {
        "person":       person,
        "total_bought": total_bought,
        "total_sold":   total_sold,
        "net_invested": round(total_bought - total_sold, 2),
        "positions":    positions,
    }


# ── /portfolio/performance ────────────────────────────────────────────────────

@app.get("/portfolio/performance")
def portfolio_performance(person: str = Query(..., min_length=1)):
    """
    Simulate a member's portfolio growth over time and compare against SPY.

    Uses the Stooq price cache in Scraping/notebooks/stocks/ (same as the
    Jupyter notebook). For uncached tickers it downloads from Stooq live.

    Returns cumulative-growth time series aligned to the member's transaction
    dates, plus SPY normalised to the same start date.
    """
    import pandas as pd
    from backend.performance import simulate_portfolio_daily

    conn = get_connection()
    try:
        variants = _name_variants(person, conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, side, tx_date, tx_estimate
            FROM transactions
            WHERE full_name = ANY(%s)
            ORDER BY tx_date ASC
        """, (variants,))
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "person": person,
            "dates": [], "portfolio_growth": [], "spy_growth": [],
            "total_return": 1.0, "cagr": 1.0,
            "spy_total_return": 1.0, "spy_cagr": 1.0,
            "start_date": None, "end_date": None,
            "n_transactions": 0,
        }

    df = pd.DataFrame(rows, columns=["ticker", "side", "tx_date", "tx_estimate"])
    result = simulate_portfolio_daily(df)
    return {"person": person, **result}


# ── /leaderboard ──────────────────────────────────────────────────────────────

ALLOWED_LB_PERIODS = {"1M", "3M", "YTD", "1Y"}


@app.get("/leaderboard")
def get_leaderboard(
    period: str = Query("1M"),
    limit:  int = Query(20, ge=5, le=50),
):
    if period not in ALLOWED_LB_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(ALLOWED_LB_PERIODS)}")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT full_name, chamber, period_return, total_invested, n_trades, n_priced
            FROM leaderboard_cache
            WHERE period = %s
            ORDER BY rank ASC
            LIMIT %s
        """, (period, limit))
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    return [
        {
            "full_name":      r[0],
            "chamber":        r[1],
            "period_return":  r[2],
            "total_invested": r[3],
            "n_trades":       r[4],
            "n_priced":       r[5],
        }
        for r in rows
    ]


# ── /top-stocks ───────────────────────────────────────────────────────────────

ALLOWED_TS_PERIODS = {"1M", "3M", "YTD", "1Y"}


@app.get("/top-stocks")
def top_stocks(
    period:  str = Query("1M"),
    top_n:   int = Query(5, ge=1, le=20),
    chamber: str | None = None,
):
    if period not in ALLOWED_TS_PERIODS:
        raise HTTPException(400, f"period must be one of {sorted(ALLOWED_TS_PERIODS)}")
    if chamber and chamber not in ALLOWED_CHAMBER:
        raise HTTPException(400, "chamber must be Senate or House")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT side, ticker, n_trades, n_members, price_change
            FROM top_stocks_cache
            WHERE period = %s AND rank <= %s
            ORDER BY side, rank ASC
        """, (period, top_n))
        rows = cur.fetchall()
    finally:
        conn.close()

    buys  = [{"ticker": r[1], "n_trades": r[2], "n_members": r[3], "price_change": r[4]} for r in rows if r[0] == "BUY"]
    sells = [{"ticker": r[1], "n_trades": r[2], "n_members": r[3], "price_change": r[4]} for r in rows if r[0] == "SELL"]

    return {"period": period, "buys": buys, "sells": sells}


# ── /scrape/trigger ───────────────────────────────────────────────────────────

@app.post("/scrape/trigger")
def trigger_scrape(job: str = Query("senate_daily")):
    """
    Manually trigger a scrape job without waiting for its next scheduled run.
    job: 'senate_daily' | 'house_daily'
    """
    try:
        from backend.scheduler import trigger_now
        ok = trigger_now(job)
        if not ok:
            raise HTTPException(404, f"Job '{job}' not found. Is the scheduler running?")
        return {"status": "triggered", "job": job}
    except ImportError:
        raise HTTPException(503, "Scheduler not available (apscheduler not installed).")
