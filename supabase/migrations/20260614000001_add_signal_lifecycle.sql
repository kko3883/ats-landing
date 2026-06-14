-- Add signal lifecycle columns to the signals table.
-- 
-- Status workflow: pending → executed → closed (SL/TP hit) | expired
-- Expired: signals older than 5 trading days with no execution
-- Closed: signals that were executed and then hit SL, TP, or were manually exited

-- 1. Add status column (nullable for backward compat — existing rows get NULL)
ALTER TABLE signals ADD COLUMN IF NOT EXISTS status TEXT;

-- 2. Add execution timestamp
ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed_at TIMESTAMPTZ;

-- 3. Add close info (when and why)
ALTER TABLE signals ADD COLUMN IF NOT EXISTS closed_at   TIMESTAMPTZ;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS close_reason TEXT;  -- stop_loss | take_profit | manual | expired

-- 4. Add position reference (link to portfolio when executed)
ALTER TABLE signals ADD COLUMN IF NOT EXISTS position_ticker TEXT;  -- the ticker as stored in portfolio table

-- 5. Index for fast filtering by status (dashboard should only show pending/executed)
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)
  WHERE status IS NOT NULL;

-- 6. Index for expiry cleanup (find old pending signals)
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)
  WHERE status = 'pending' OR status IS NULL;

-- 7. Backfill: mark all existing signals as 'expired' (they're from past runs)
UPDATE signals SET status = 'expired' WHERE status IS NULL;