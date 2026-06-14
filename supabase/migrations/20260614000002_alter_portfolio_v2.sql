-- Add v2 columns to the existing portfolio table
-- (Table was created ad-hoc in dashboard; this adds the columns sync_positions.py v2 needs)

ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS avg_cost        DOUBLE PRECISION;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS last_price      DOUBLE PRECISION;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS unrealized_pnl  DOUBLE PRECISION;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS allocation_pct  DOUBLE PRECISION;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS vix_zone        TEXT;
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS snapshot_at     TIMESTAMPTZ DEFAULT now();

-- If the old column was 'snapshot_at', keep it; if missing, add it
-- (Some versions of the table had snapshot_at from original sync)