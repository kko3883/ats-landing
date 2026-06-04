#!/usr/bin/env python3
"""Create indicator_signals table via direct psql connection."""
import subprocess
import urllib.parse

# Fetch password from keychain
result = subprocess.run(
    ["security", "find-generic-password", "-w", "-s", "ats-supabase", "-a", "db_password"],
    capture_output=True, text=True, check=True,
)
db_pass = result.stdout.strip()

# Try different pooler ports
for port in [5432, 6543]:
    conn_uri = f"postgresql://postgres.nwatzlrmoefluymhqgwi:{urllib.parse.quote(db_pass, safe='')}@aws-0-ap-southeast-1.pooler.supabase.com:{port}/postgres?sslmode=require"
    
    sql = "SELECT 1 as test"
    psql = "/opt/homebrew/opt/libpq/bin/psql"
    cmd = [psql, conn_uri, "-c", sql]
    
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print(f"Port {port}: Connected!")
            # Now run the actual table creation
            create_sql = """
CREATE TABLE IF NOT EXISTS indicator_signals (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    market TEXT NOT NULL,
    bar_size TEXT NOT NULL DEFAULT '60m',
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    macd_value DOUBLE PRECISION,
    macd_signal TEXT,
    adx_value DOUBLE PRECISION,
    adx_signal TEXT,
    rsi_value DOUBLE PRECISION,
    rsi_signal TEXT,
    stoch_k DOUBLE PRECISION,
    stoch_d DOUBLE PRECISION,
    stoch_signal TEXT,
    mfi_value DOUBLE PRECISION,
    mfi_signal TEXT,
    obv_slope DOUBLE PRECISION,
    obv_signal TEXT,
    bb_percent_b DOUBLE PRECISION,
    bb_signal TEXT,
    composite_score INTEGER,
    composite_signal TEXT,
    ticker_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_indicator_ticker ON indicator_signals(ticker, calculated_at DESC);
ALTER TABLE indicator_signals ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS anon_select ON indicator_signals FOR SELECT USING (true);
"""
            r2 = subprocess.run([psql, conn_uri, "-c", create_sql], capture_output=True, text=True, timeout=30)
            if r2.returncode == 0:
                print("Table created successfully!")
                print(r2.stdout)
            else:
                print(f"Create table error: {r2.stderr}")
            break
        else:
            print(f"Port {port}: {r.stderr[:100]}")
    except Exception as e:
        print(f"Port {port}: {e}")
