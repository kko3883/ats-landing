"""
Prepare the labeled training dataset for the XGBoost entry classifier.

Workflow:
  1. Fetch historical D1 and M15 bars for seed universe symbols
  2. Compute Layer 1 SMA(200) for each symbol
  3. For each M15 bar (rolling window), compute Layer 2 features
  4. Label = 1 if the return over the next N bars > 0 (N = forecast_bars)
  5. Output features + labels as Parquet for train_xgb.py

This mirrors EXACTLY what the live inference (feature_engine.py) computes.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..config import STATE_DIR
from ..data.historical_fetcher import build_multi_timeframe
from ..layer1_macro.daily_filter import compute_sma
from ..layer2_tactical.feature_engine import FEATURE_NAMES, FeatureEngine

logger = logging.getLogger(__name__)

OUTPUT_DIR = STATE_DIR / "datasets"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def prepare_dataset(
    symbols: list[str],
    d1_count: int = 300,
    m15_count: int = 500,
    forecast_bars: int = 4,
    min_bars: int = 50,
    force_refresh: bool = False,
) -> Optional[pd.DataFrame]:
    """
    Build the full training dataset.

    Returns DataFrame with feature columns + 'target' column (0/1).
    Returns None if no data is available.
    """
    print(f"Preparing dataset for {len(symbols)} symbols...")
    print(f"  D1 bars: {d1_count}, M15 bars: {m15_count}, forecast: {forecast_bars} bars")

    # Fetch multi-timeframe data
    data = build_multi_timeframe(
        symbols,
        d1_count=d1_count,
        m15_count=m15_count,
        m1_count=0,  # Not needed for training
        force_refresh=force_refresh,
    )

    feature_engine = FeatureEngine()
    all_rows = []

    for sym in symbols:
        sym_data = data.get(sym, {})
        d1_df = sym_data.get("D1")
        m15_df = sym_data.get("M15")

        if m15_df is None or m15_df.empty:
            print(f"  ⚠ {sym}: no M15 data, skipping")
            continue

        # Normalize column names
        m15_df = m15_df.rename(columns={c: c.lower() for c in m15_df.columns})

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(m15_df.columns):
            print(f"  ⚠ {sym}: missing columns {required - set(m15_df.columns)}, skipping")
            continue

        closes = m15_df["close"].values
        highs = m15_df["high"].values
        lows = m15_df["low"].values
        volumes = m15_df["volume"].values.astype(float)

        # Compute SMA(200) from D1 data (or approximate from M15)
        sma200 = None
        if d1_df is not None and not d1_df.empty:
            d1_df = d1_df.rename(columns={c: c.lower() for c in d1_df.columns})
            if "close" in d1_df.columns:
                sma200 = compute_sma(d1_df["close"], 200)

        # Rolling window: for each bar (starting at min_bars), compute features
        n_bars = len(closes)
        last_valid = n_bars - forecast_bars  # Need forecast_bars of future data for label

        for i in range(min_bars, last_valid):
            # Window: bars [0 : i+1] (inclusive of current bar for feature comp)
            window_close = closes[max(0, i - m15_count): i + 1]
            window_high = highs[max(0, i - m15_count): i + 1]
            window_low = lows[max(0, i - m15_count): i + 1]
            window_vol = volumes[max(0, i - m15_count): i + 1]

            if len(window_close) < 30:
                continue

            # Compute features
            fv = feature_engine.compute(
                symbol=sym,
                closes=window_close,
                highs=window_high,
                lows=window_low,
                volumes=window_vol,
                sma200=sma200,
            )

            # Label: return over next forecast_bars
            future_close = closes[i + forecast_bars] if i + forecast_bars < n_bars else closes[-1]
            current_price = closes[i]
            if current_price > 0:
                future_return = (future_close - current_price) / current_price
                target = 1 if future_return > 0 else 0
            else:
                target = 0

            row = fv.to_dict()
            row["symbol"] = sym
            row["target"] = target
            row["future_return"] = round(float(future_return), 6)
            row["price"] = float(current_price)
            row["timestamp"] = m15_df["timestamp"].iloc[i].isoformat()

            all_rows.append(row)

    if not all_rows:
        print("  No training rows generated.")
        return None

    df = pd.DataFrame(all_rows)

    # Basic stats
    print(f"\nDataset complete: {len(df)} rows")
    print(f"  Positive labels: {df['target'].sum()} ({df['target'].mean():.1%})")
    print(f"  Features: {FEATURE_NAMES}")
    print(f"  Symbols: {df['symbol'].nunique()}")

    # Save
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"training_dataset_{ts}.parquet"
    df.to_parquet(out_path, index=False)
    print(f"  Saved: {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # Also save a symbolic link to "latest"
    latest_path = OUTPUT_DIR / "training_dataset_latest.parquet"
    if latest_path.exists() or latest_path.is_symlink():
        latest_path.unlink()
    latest_path.symlink_to(out_path.name)

    return df


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from equity_engine.config import EngineConfig
    cfg = EngineConfig.from_defaults()
    symbols = sys.argv[1:] if len(sys.argv) > 1 else cfg.seed_universe[:10]
    prepare_dataset(symbols, force_refresh=True)
