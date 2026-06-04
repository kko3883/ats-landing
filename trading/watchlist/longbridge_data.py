"""
Longbridge data source for HK and US market stocks.

Fetches historical OHLCV data via longbridge CLI (--format json) and
converts to the same MultiIndex DataFrame format as yfinance so the
existing pipeline (macro_betas, cross_sectional_rs) works unchanged.

Uses ThreadPoolExecutor for parallel symbol requests.
"""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path.home() / ".hermes" / "trading" / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _longbridge_kline_history(symbol: str, start: str, end: str) -> list[dict] | None:
    """Fetch kline history for one symbol via longbridge CLI."""
    try:
        result = subprocess.run(
            ["longbridge", "kline", "history", symbol,
             "--start", start, "--end", end, "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def _candle_to_row(candle: dict, sym: str) -> dict | None:
    """Convert a single Longbridge candle dict into OHLCV row."""
    try:
        close = float(candle["close"])
        open_ = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        volume = float(candle["volume"])

        dt = datetime.strptime(candle["time"], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(hour=0, minute=0, second=0)

        return {
            "Date": dt,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
            "Symbol": sym,
        }
    except (KeyError, ValueError, TypeError):
        return None


def fetch_longbridge_data(
    symbols: list[str],
    suffix: str = "",
    start: str = "2025-06-01",
    end: str | None = None,
    max_workers: int = 8,
    label: str = "",
) -> pd.DataFrame:
    """
    Fetch OHLCV data for any symbols via Longbridge CLI.

    Appends suffix to each symbol if provided (e.g. '.US', '.HK').
    Returns a MultiIndex DataFrame matching yfinance format:
        Columns: (Price, Ticker) — e.g. ("Close", "0700.HK")
        Index: datetime dates
    """
    if suffix:
        symbols = [f"{s}{suffix}" if not s.endswith(suffix) else s for s in symbols]

    return _fetch_batch(symbols, start, end, max_workers, label)


def _fetch_batch(
    symbols: list[str],
    start: str = "2025-06-01",
    end: str | None = None,
    max_workers: int = 8,
    label: str = "",
) -> pd.DataFrame:
    """Inner batch fetch — shared by all markets."""
    if end is None:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    tag = label or f"{len(symbols)} symbols"
    print(f"  Fetching {tag} via Longbridge ({max_workers} workers)...")
    t0 = time.time()

    symbols = list(dict.fromkeys(symbols))

    rows = []
    failed = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {
            pool.submit(_longbridge_kline_history, sym, start, end): sym
            for sym in symbols
        }
        for fut in as_completed(fut_map):
            sym = fut_map[fut]
            try:
                candles = fut.result()
                if not candles:
                    failed.append(sym)
                    continue
                for c in candles:
                    row = _candle_to_row(c, sym)
                    if row:
                        rows.append(row)
            except Exception:
                failed.append(sym)

    elapsed = time.time() - t0
    ok = len(symbols) - len(failed)
    print(f"  Done in {elapsed:.1f}s ({ok} ok, {len(failed)} failed)")
    if failed:
        print(f"  Failed: {', '.join(failed[:10])}{'...' if len(failed) > 10 else ''}")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.sort_values(["Date", "Symbol"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    pivoted = df.pivot_table(
        index="Date",
        columns="Symbol",
        values=[col for col in ["Open", "High", "Low", "Close", "Volume", "Adj Close"]
                if col in df.columns],
        aggfunc="first",
    )
    pivoted.columns.names = ["Price", "Ticker"]

    for sym in pivoted.columns.get_level_values("Ticker").unique():
        if "Adj Close" not in pivoted.xs(sym, level="Ticker", axis=1).columns:
            pivoted[("Adj Close", sym)] = pivoted[("Close", sym)]

    return pivoted.sort_index(axis=1)
