"""
Historical data fetcher using Longbridge CLI.

Fetches D1, M15, and M1 OHLCV bars for a list of symbols.
Caches results as Parquet files to avoid re-fetching.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from ..config import ENGINE_DIR, STATE_DIR

CACHE_DIR = STATE_DIR / "hist_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Longbridge period → max count allowed
PERIOD_LIMITS = {
    "1m": 1200,
    "5m": 1200,
    "15m": 1200,
    "30m": 1200,
    "60m": 1200,
    "1d": 500,
    "1w": 260,
    "1M": 120,
}


def fetch_kline_raw(
    symbol: str,
    period: str = "1d",
    count: int = 300,
) -> Optional[pd.DataFrame]:
    """
    Fetch historical candlestick data via Longbridge CLI.

    Args:
        symbol: e.g. 'AAPL.US', 'TSLA.US'
        period: '1m', '5m', '15m', '30m', '60m', '1d', '1w', '1M'
        count: number of candles (max varies by period)

    Returns DataFrame with columns: timestamp, open, high, low, close, volume, turnover
    Returns None on failure.
    """
    max_count = PERIOD_LIMITS.get(period, 1200)
    count = min(count, max_count)

    try:
        result = subprocess.run(
            [
                "longbridge", "kline", symbol,
                "--period", period,
                "--count", str(count),
                "--format", "json",
            ],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"  ⚠ {symbol} {period}: timeout after 60s")
        return None

    if result.returncode != 0:
        print(f"  ⚠ {symbol} {period}: CLI error — {result.stderr[:200]}")
        return None

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  ⚠ {symbol} {period}: JSON parse error — {e}")
        return None

    if not raw:
        return None

    rows = []
    for k in raw:
        rows.append({
            "timestamp": pd.to_datetime(k["timestamp"]),
            "open": float(k["open"]),
            "high": float(k["high"]),
            "low": float(k["low"]),
            "close": float(k["close"]),
            "volume": int(k.get("volume", 0)),
            "turnover": float(k.get("turnover", 0)),
        })

    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return df


def fetch_and_cache(
    symbol: str,
    period: str,
    count: int = 300,
    force_refresh: bool = False,
) -> Optional[pd.DataFrame]:
    """
    Fetch kline data, caching to Parquet.  Returns DataFrame or None.

    Cache key: symbol_period_count.parquet
    """
    cache_key = f"{symbol.replace('.', '_')}_{period}_{count}.parquet"
    cache_path = CACHE_DIR / cache_key

    if not force_refresh and cache_path.exists():
        try:
            df = pd.read_parquet(cache_path)
            # Check if cache is stale (>1 day for D1, >1 hour for intraday)
            if not df.empty:
                last_ts = df["timestamp"].max()
                age = datetime.now(timezone.utc) - last_ts.replace(tzinfo=timezone.utc)
                max_age = timedelta(days=1) if period.endswith("d") else timedelta(hours=1)
                if age < max_age:
                    return df
        except Exception:
            pass  # cache corrupt — re-fetch

    df = fetch_kline_raw(symbol, period, count)
    if df is not None and not df.empty:
        try:
            df.to_parquet(cache_path, index=False)
        except Exception as e:
            print(f"  ⚠ {symbol} {period}: cache write failed — {e}")
    return df


def build_multi_timeframe(
    symbols: list[str],
    d1_count: int = 300,
    m15_count: int = 500,
    m1_count: int = 500,
    force_refresh: bool = False,
) -> dict[str, dict[str, Optional[pd.DataFrame]]]:
    """
    Fetch D1, M15, M1 bars for a list of symbols.

    Returns:
        {
            "AAPL.US": {
                "D1": DataFrame or None,
                "M15": DataFrame or None,
                "M1": DataFrame or None,
            },
            ...
        }
    """
    result = {}
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i}/{total}] {sym} ...")
        sym_data = {}
        for period, count in [("1d", d1_count), ("15m", m15_count), ("1m", m1_count)]:
            label = "D1" if period == "1d" else ("M15" if period == "15m" else "M1")
            df = fetch_and_cache(sym, period, count, force_refresh)
            sym_data[label] = df
            if df is not None and not df.empty:
                print(f"    {label}: {len(df)} bars ({df['timestamp'].min()} → {df['timestamp'].max()})")
            else:
                print(f"    {label}: NO DATA")
        result[sym] = sym_data
    return result


# ── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL.US", "TSLA.US"]
    print(f"Fetching multi-timeframe data for {len(symbols)} symbols...")
    data = build_multi_timeframe(symbols, force_refresh=True)
    for sym, frames in data.items():
        print(f"\n{sym}:")
        for tf, df in frames.items():
            status = f"{len(df)} bars" if df is not None else "NO DATA"
            print(f"  {tf}: {status}")