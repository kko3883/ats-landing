-- Add ATR, stop-loss, take-profit, and Bollinger band columns
-- for the dashboard entry cards feature.

ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS atr_value DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS stop_loss DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS take_profit DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS bb_upper DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS bb_lower DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS bb_mid DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS current_price DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS entry_zone_low DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS entry_zone_high DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS risk_reward DOUBLE PRECISION;
ALTER TABLE indicator_signals ADD COLUMN IF NOT EXISTS suggested_size INTEGER;