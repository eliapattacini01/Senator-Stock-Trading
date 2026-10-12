"""
/portfolio/performance and /portfolio must be served from the database only:
no yfinance, no outbound network. The DB is faked, so no DATABASE_URL is needed.

    pip install -r requirements-dev.txt
    pytest
"""
import datetime as dt
import socket
import sys
import types

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

PERSON = "Jane Doe"
TODAY = dt.date.today()
BDAYS = [d.date() for d in pd.bdate_range("2023-11-01", TODAY)]
PRICES = {
    "AAPL": [100 + 0.10 * i for i in range(len(BDAYS))],
    "MSFT": [200 + 0.05 * i for i in range(len(BDAYS))],
    "SPY":  [400 + 0.20 * i for i in range(len(BDAYS))],
}
TRANSACTIONS = [  # ticker, side, tx_date, file_date, tx_estimate
    ("AAPL",     "BUY",  dt.date(2024, 1, 2), dt.date(2024, 1, 20), 15000),
    ("MSFT",     "BUY",  dt.date(2024, 2, 1), dt.date(2024, 2, 15), 32500),
    ("DELISTED", "BUY",  dt.date(2024, 3, 1), dt.date(2024, 3, 10), 8000),   # no stored prices
    ("AAPL",     "SELL", dt.date(2024, 6, 3), dt.date(2024, 6, 20), 8000),
]
PERFORMANCE_KEYS = {
    "person", "dates", "portfolio_growth", "spy_growth", "total_return", "cagr",
    "spy_total_return", "spy_cagr", "start_date", "end_date", "n_transactions",
    "disclosure_dates", "disclosure_growth", "disclosure_total_return", "disclosure_cagr",
}


class FakeDB:
    """Answers the handful of queries these endpoints run."""

    def __init__(self):
        self.queries: list[str] = []

    def connect(self):
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self, binary=False):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def close(self):
        pass


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows: list = []

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.db.queries.append(q)
        if "DISTINCT full_name" in q:
            self.rows = [(PERSON,)]
        elif "FROM transactions" in q and "file_date" in q:
            self.rows = list(TRANSACTIONS)
        elif "FROM transactions" in q:
            self.rows = [(t, s, d, e) for t, s, d, _, e in TRANSACTIONS]
        elif "LATERAL" in q:  # latest close per ticker
            self.rows = [(t, PRICES[t][-1]) for t in params[0] if t in PRICES]
        elif "FROM prices" in q:  # one row per ticker: days since epoch[], prices[]
            epoch = dt.date(1970, 1, 1)
            self.rows = [(t, [(d - epoch).days for d in BDAYS], PRICES[t])
                         for t in params[0] if t in PRICES]
        else:
            raise AssertionError(f"unexpected query: {q}")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


@pytest.fixture
def forbidden_calls(monkeypatch):
    """Replace yfinance and outbound sockets with tripwires; returns the list of violations."""
    calls: list[str] = []

    fake_yf = types.ModuleType("yfinance")

    def _tripwire(name):
        def _call(*args, **kwargs):
            calls.append(f"yfinance.{name}")
            raise RuntimeError("yfinance must not be used while serving a request")
        return _call

    fake_yf.__getattr__ = _tripwire  # any attribute (download, Ticker, …) trips
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    loopback = {"127.0.0.1", "::1", "localhost"}
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def _guard(real):
        def connect(self, address):
            if isinstance(address, tuple) and address[0] not in loopback:
                calls.append(f"network connect {address}")
                raise OSError("outbound network is disabled in this test")
            return real(self, address)
        return connect

    def getaddrinfo(host, *args, **kwargs):
        if host not in loopback and host is not None:
            calls.append(f"DNS lookup {host}")
            raise OSError("outbound network is disabled in this test")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _guard(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(real_connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return calls


@pytest.fixture
def api(monkeypatch, forbidden_calls):
    import backend.main as main
    import backend.performance as performance

    db = FakeDB()
    monkeypatch.setattr(main, "get_connection", db.connect)
    monkeypatch.setattr(performance, "get_connection", db.connect)
    main._ttl_cache.clear()
    performance._cache.clear()
    main.limiter.reset()
    return main, TestClient(main.app), db


def test_performance_reads_prices_only_from_db(api, forbidden_calls):
    _, client, db = api

    r = client.get("/portfolio/performance", params={"person": PERSON})

    assert r.status_code == 200, r.text
    assert forbidden_calls == []
    body = r.json()
    assert set(body) == PERFORMANCE_KEYS
    assert body["person"] == PERSON
    assert body["dates"] and len(body["dates"]) == len(body["portfolio_growth"]) == len(body["spy_growth"])
    assert len(body["disclosure_dates"]) == len(body["disclosure_growth"]) > 0
    assert body["start_date"] == "2024-01-02"
    assert body["n_transactions"] == 3  # DELISTED has no prices and is skipped
    assert any("FROM prices" in q for q in db.queries)


def test_performance_response_is_cached(api, forbidden_calls):
    _, client, db = api

    first = client.get("/portfolio/performance", params={"person": PERSON})
    n_queries = len(db.queries)
    second = client.get("/portfolio/performance", params={"person": PERSON})

    assert second.status_code == 200
    assert second.content == first.content
    assert len(db.queries) == n_queries  # served from the response cache
    assert forbidden_calls == []


def test_portfolio_current_price_comes_from_db(api, forbidden_calls):
    _, client, _ = api

    r = client.get("/portfolio", params={"person": PERSON, "fetch_prices": "true"})

    assert r.status_code == 200, r.text
    assert forbidden_calls == []
    prices = {p["ticker"]: p["current_price"] for p in r.json()["positions"]}
    assert prices == {"AAPL": PRICES["AAPL"][-1], "MSFT": PRICES["MSFT"][-1], "DELISTED": None}


def test_rate_limit_is_per_client_ip(api):
    _, client, _ = api
    bot = {"CF-Connecting-IP": "187.124.179.101"}

    codes = [client.get("/portfolio/performance", params={"person": PERSON}, headers=bot).status_code
             for _ in range(7)]
    other = client.get("/portfolio/performance", params={"person": PERSON},
                       headers={"CF-Connecting-IP": "203.0.113.7"})

    assert codes == [200] * 6 + [429]
    assert other.status_code == 200


def test_blocked_ips(api, monkeypatch):
    main, client, _ = api
    monkeypatch.setattr(main, "_BLOCKED_IPS", {"187.124.179.101"})

    assert client.get("/health", headers={"CF-Connecting-IP": "187.124.179.101"}).status_code == 403
    assert client.get("/health", headers={"CF-Connecting-IP": "203.0.113.7"}).status_code == 200


def _request(headers: dict) -> Request:
    return Request({
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("10.0.0.1", 1234),
    })


def test_client_ip_resolution():
    from backend.main import _client_ip

    xff = {"X-Forwarded-For": "198.51.100.4, 10.0.0.2"}
    assert _client_ip(_request({"CF-Connecting-IP": "203.0.113.7", **xff})) == "203.0.113.7"
    assert _client_ip(_request(xff)) == "198.51.100.4"
    assert _client_ip(_request({})) == "10.0.0.1"
