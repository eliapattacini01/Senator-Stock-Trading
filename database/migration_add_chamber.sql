-- Migration: add chamber column to transactions table
-- Run this once against your postgres database:
--   psql -U postgres -d senate_stocks -f database/migration_add_chamber.sql

ALTER TABLE public.transactions
    ADD COLUMN IF NOT EXISTS chamber VARCHAR(20) DEFAULT 'Senate';

UPDATE public.transactions
    SET chamber = 'Senate'
    WHERE chamber IS NULL;

CREATE INDEX IF NOT EXISTS transactions_chamber_idx
    ON public.transactions (chamber);
