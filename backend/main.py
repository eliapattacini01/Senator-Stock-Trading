from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Query, HTTPException
from backend.db import get_connection

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        # we'll add your Netlify URL later
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_SORT = {"tx_date", "tx_estimate", "ticker", "full_name", "side"}
ALLOWED_ORDER = {"asc", "desc"}

@app.get("/transactions")
def get_transactions(
    senator: str | None = None,
    side: str | None = None,
    ticker: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = "tx_date",
    order: str = "desc",
):
    sort = sort.lower()
    order = order.lower()

    if sort not in ALLOWED_SORT:
        raise HTTPException(status_code=400, detail=f"Invalid sort. Use one of: {sorted(ALLOWED_SORT)}")
    if order not in ALLOWED_ORDER:
        raise HTTPException(status_code=400, detail="Invalid order. Use asc or desc.")

    conn = get_connection()
    try:
        cur = conn.cursor()

        sql = """
            SELECT full_name, ticker, side, tx_date, tx_estimate
            FROM transactions
            WHERE 1=1
        """
        params = []

        if senator:
            sql += " AND full_name = %s"
            params.append(senator)

        if side:
            sql += " AND side = %s"
            params.append(side)

        if ticker:
            sql += " AND ticker = %s"
            params.append(ticker)

        # Safe because sort/order are validated from allow-lists
        sql += f" ORDER BY {sort} {order}"
        sql += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(sql, params)
        rows = cur.fetchall()

        return [
            {
                "full_name": r[0],
                "ticker": r[1],
                "side": r[2],
                "tx_date": r[3],
                "tx_estimate": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()

@app.get("/senators")
def get_senators(limit: int = 200):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT full_name
            FROM transactions
            ORDER BY full_name ASC
            LIMIT %s;
            """,
            (limit,)
        )
        rows = cur.fetchall()
        return [{"full_name": r[0]} for r in rows]
    finally:
        conn.close()

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # later restrict
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/transactions/count")
def count_transactions(
    senator: str | None = None,
    side: str | None = None,
    ticker: str | None = None,
):
    conn = get_connection()
    try:
        cur = conn.cursor()

        sql = "SELECT COUNT(*) FROM transactions WHERE 1=1"
        params = []

        if senator:
            sql += " AND full_name = %s"
            params.append(senator)
        if side:
            sql += " AND side = %s"
            params.append(side)
        if ticker:
            sql += " AND ticker = %s"
            params.append(ticker)

        cur.execute(sql, params)
        total = cur.fetchone()[0]
        return {"total": total}
    finally:
        conn.close()

ALLOWED_PERIOD = {"week", "month", "year"}
ALLOWED_SIDE = {"BUY", "SELL"}

@app.get("/activity/top")
def top_activity(
    period: str = Query("week"),
    side: str = Query("BUY"),
    top_n: int = Query(10, ge=1, le=50),
    start: str | None = None,   # "YYYY-MM-DD"
    end: str | None = None,     # "YYYY-MM-DD"
):
    """
    Returns top tickers per time bucket (week/month/year) by number of unique senators.
    """

    period = period.lower()
    side = side.upper()

    if period not in ALLOWED_PERIOD:
        raise HTTPException(status_code=400, detail=f"period must be one of {sorted(ALLOWED_PERIOD)}")
    if side not in ALLOWED_SIDE:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")

    # Map your period choice to a PostgreSQL date_trunc unit
    # date_trunc('week', tx_date) groups all dates into weekly buckets
    unit = {"week": "week", "month": "month", "year": "year"}[period]

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Build SQL with optional date filters.
        # NOTE: We do NOT parameterize 'unit' because Postgres doesn't accept it as a bind param in date_trunc.
        # It's safe because we whitelist unit above.
        sql = f"""
            WITH bucketed AS (
                SELECT
                    date_trunc('{unit}', tx_date)::date AS bucket_start,
                    ticker,
                    COUNT(DISTINCT full_name) AS n_senators,
                    COUNT(*) AS n_trades,
                    COALESCE(SUM(tx_estimate), 0) AS total_estimate
                FROM transactions
                WHERE side = %s
        """
        params = [side]

        # Optional filters
        if start:
            sql += " AND tx_date >= %s"
            params.append(start)
        if end:
            sql += " AND tx_date <= %s"
            params.append(end)

        sql += """
                GROUP BY 1, 2
            ),
            ranked AS (
                SELECT
                    bucket_start,
                    ticker,
                    n_senators,
                    n_trades,
                    total_estimate,
                    ROW_NUMBER() OVER (
                        PARTITION BY bucket_start
                        ORDER BY n_senators DESC, n_trades DESC, total_estimate DESC, ticker ASC
                    ) AS rnk
                FROM bucketed
            )
            SELECT bucket_start, ticker, n_senators, n_trades, total_estimate
            FROM ranked
            WHERE rnk <= %s
            ORDER BY bucket_start DESC, n_senators DESC, ticker ASC;
        """
        params.append(top_n)

        cur.execute(sql, params)
        rows = cur.fetchall()

        return [
            {
                "bucket_start": r[0].isoformat(),  # date -> "YYYY-MM-DD"
                "ticker": r[1],
                "n_senators": r[2],
                "n_trades": r[3],
                "total_estimate": r[4],
            }
            for r in rows
        ]
    finally:
        conn.close()

@app.get("/timeseries/monthly")
def monthly_timeseries(
    ticker: str = Query(..., min_length=1),
    mode: str = Query("both")  # "buy" | "sell" | "both"
):
    """
    Monthly time-series for one ticker:
    - x axis: month_start
    - y axis: number of unique senators
    mode:
      - buy  -> returns buy_senators only
      - sell -> returns sell_senators only
      - both -> returns both series
    """
    mode = mode.lower()
    if mode not in {"buy", "sell", "both"}:
        raise HTTPException(status_code=400, detail="mode must be buy, sell, or both")

    tkr = ticker.strip().upper()

    conn = get_connection()
    try:
        cur = conn.cursor()

        # We compute both buy and sell series in one query.
        # Then frontend can decide whether to show one or both.
        sql = """
            SELECT
                date_trunc('month', tx_date)::date AS month_start,
                COUNT(DISTINCT CASE WHEN side = 'BUY'  THEN full_name END) AS buy_senators,
                COUNT(DISTINCT CASE WHEN side = 'SELL' THEN full_name END) AS sell_senators
            FROM transactions
            WHERE ticker = %s
            GROUP BY 1
            ORDER BY 1;
        """
        cur.execute(sql, (tkr,))
        rows = cur.fetchall()

        out = [
            {
                "month_start": r[0].isoformat(),
                "buy_senators": r[1] or 0,
                "sell_senators": r[2] or 0,
            }
            for r in rows
        ]

        # Optional: if user asked only buy/sell, you can still return both,
        # but to keep payload smaller, you can strip the unused one:
        if mode == "buy":
            for d in out:
                d.pop("sell_senators", None)
        elif mode == "sell":
            for d in out:
                d.pop("buy_senators", None)

        return out
    finally:
        conn.close()

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
            LIMIT %s;
        """, (limit,))
        rows = cur.fetchall()
        return [{"ticker": r[0]} for r in rows]
    finally:
        conn.close()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        # add your deployed frontend URL later, e.g. "https://your-site.netlify.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
