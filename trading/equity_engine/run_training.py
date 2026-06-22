#!/usr/bin/env python3
"""
One-command training pipeline — prepare dataset + train + validate.

Automates the full ML lifecycle:
  1. Fetch historical data (Longbridge CLI or yfinance fallback)
  2. Engineer features and labels
  3. Train XGBoost with Optuna hyperparameter optimization
  4. Save model + metadata to models/
  5. Run smoke test to validate

Usage:
    python equity_engine/run_training.py                           # seed universe (50 stocks)
    python equity_engine/run_training.py --symbols AAPL.US,MSFT.US  # specific stocks
    python equity_engine/run_training.py --quick                     # fast: 5 stocks, 50 trials
    python equity_engine/run_training.py --skip-prepare              # use existing dataset
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from equity_engine.config import EngineConfig

def main():
    parser = argparse.ArgumentParser(description="Train XGBoost model for equity engine")
    parser.add_argument("--symbols", default="",
                        help="Comma-separated list of tickers (default: seed universe)")
    parser.add_argument("--quick", action="store_true",
                        help="Fast mode: 5 stocks, 50 Optuna trials")
    parser.add_argument("--skip-prepare", action="store_true",
                        help="Skip dataset preparation (use existing)")
    parser.add_argument("--trials", type=int, default=100,
                        help="Number of Optuna trials (default: 100)")
    parser.add_argument("--days", type=int, default=300,
                        help="Days of historical data to fetch")
    args = parser.parse_args()

    cfg = EngineConfig.from_defaults()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    elif args.quick:
        symbols = cfg.seed_universe[:5]
    else:
        symbols = cfg.seed_universe

    print("=" * 70)
    print(f"  EQUITY ENGINE — TRAINING PIPELINE")
    print(f"  Symbols: {len(symbols)} stocks")
    print(f"  Mode: {'QUICK' if args.quick else 'FULL'}")
    print(f"  Trials: {args.trials}")
    print("=" * 70)

    t0 = time.time()

    # ── Step 1: Prepare dataset ──────────────────────────────────────────
    if not args.skip_prepare:
        print(f"\n── Step 1/3: Preparing dataset ({len(symbols)} symbols) ──")
        from equity_engine.training.prepare_dataset import prepare_dataset
        df = prepare_dataset(
            symbols,
            d1_count=args.days,
            m15_count=min(500, args.days * 4),
            forecast_bars=cfg.layer2.forecast_bars,
            min_bars=50,
            force_refresh=False,  # use cache if available
        )
        if df is None or len(df) < 100:
            print("  ⚠ Dataset too small for training. Try more symbols or days.")
            sys.exit(1)
        print(f"  Dataset: {len(df)} rows ready")
    else:
        print("\n── Step 1/3: Skipping dataset preparation ──")
        df = None

    # ── Step 2: Train model ─────────────────────────────────────────────
    print(f"\n── Step 2/3: Training XGBoost ({args.trials} trials) ──")
    from equity_engine.training.train_xgb import train_xgb
    model = train_xgb(df, n_trials=args.trials)

    if model is None:
        print("  ⚠ Training failed. Check logs above.")
        sys.exit(1)

    t1 = time.time()
    print(f"\n  Training completed in {t1 - t0:.0f}s")

    # ── Step 3: Smoke test validation ───────────────────────────────────
    print(f"\n── Step 3/3: Smoke test validation ──")
    from equity_engine.run_smoke_test import run_smoke_test
    test_symbols = symbols[:3]  # validate on first 3
    result = run_smoke_test(test_symbols, days=min(60, args.days))

    print(f"\n{'=' * 70}")
    print(f"  PIPELINE COMPLETE")
    print(f"  Total time: {time.time() - t0:.0f}s")
    print(f"  Model: models/xgb_entry_classifier.json")
    print(f"")
    print(f"  Next steps:")
    print(f"    python equity_engine/run_smoke_test.py --days 60")
    print(f"    python equity_engine/run_live.py --paper --no-execute")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()