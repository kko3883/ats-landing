-- Add universe_count column to equity_state for pipeline visualization.
ALTER TABLE equity_state ADD COLUMN IF NOT EXISTS universe_count INTEGER NOT NULL DEFAULT 0;