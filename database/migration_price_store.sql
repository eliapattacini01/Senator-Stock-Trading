-- Migration: price store for the DB-only /portfolio/performance endpoint.
-- Idempotent: safe to run more than once.
-- Run it once against Neon, either pasting it into the Neon SQL Editor or with:
--   psql "$DATABASE_URL" -f database/migration_price_store.sql
--   python crons/backfill_prices.py --migrate-only
-- crons/refresh_prices.py and crons/backfill_prices.py also apply it on start.

-- Daily adjusted close per ticker (Yahoo Finance, auto_adjust=True).
-- Already present on production; kept here so a fresh database works.
CREATE TABLE IF NOT EXISTS public.prices (
    ticker VARCHAR(20)      NOT NULL,
    date   DATE             NOT NULL,
    price  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (ticker, date)
);

-- Download state per ticker, maintained by backend/price_updater.py.
--   unavailable   TRUE  -> delisted / unknown on Yahoo: the daily job skips it
--   fail_count          -> consecutive runs that returned no new data
--   history_from        -> earliest date requested by the last full download
-- To retry a ticker:  UPDATE ticker_status SET unavailable = FALSE, fail_count = 0 WHERE ticker = 'XYZ';
CREATE TABLE IF NOT EXISTS public.ticker_status (
    ticker        VARCHAR(20) PRIMARY KEY,
    unavailable   BOOLEAN     NOT NULL DEFAULT FALSE,
    reason        TEXT,
    fail_count    INT         NOT NULL DEFAULT 0,
    last_attempt  TIMESTAMPTZ,
    last_success  TIMESTAMPTZ,
    history_from  DATE
);

-- One-time carry-over of the legacy skip list (only while ticker_status is empty).
-- ticker_skip itself is left untouched and is no longer used.
DO $$
BEGIN
    IF to_regclass('public.ticker_skip') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM public.ticker_status) THEN
        INSERT INTO public.ticker_status (ticker, unavailable, reason, last_attempt)
        SELECT ticker, TRUE, 'legacy ticker_skip', failed_at
        FROM public.ticker_skip
        ON CONFLICT (ticker) DO NOTHING;
    END IF;
END $$;
