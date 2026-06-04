#!/usr/bin/env python3
"""
ATS Watchlist Screener — 4-Stage Quantitative Pipeline.

The institutional-grade approach:
  Stage 1: Liquidity guardrails (price, dollar volume)
  Stage 2: Macro factor betas (VIX, DXY) via OLS regression
  Stage 3: Cross-sectional relative strength within beta groups
  Stage 4: Flow/event overlay (deferred — needs paid data)

Your regime detector then activates specific groups:
  Risk-On  → Q1, Q2 (most negative VIX beta = growth/tech)
  Choppy   → Q3, Q4  (moderate beta = stable compounders)
  Risk-Off → Q5 long + Q1/Q2 short candidates

Usage:
    python -m watchlist.screener --markets us,hk --top 50
    python -m watchlist.screener --markets us --top 30
    python -m watchlist.screener --refresh      # Clear cache
"""

import argparse
import json
import time
from datetime import datetime, timezone

import numpy as np

from .config import MARKETS, load_universe, OUTPUT_DIR, OUTPUT_FILE, VIX_BETA_GROUP_LABELS
from .data_source import fetch_price_data, invalidate_cache
from .macro_betas import run_stage2_and_3


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder for numpy types."""

    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return super().default(obj)


def screen_market(
    market: str,
    top_n: int = 50,
    force_refresh: bool = False,
) -> dict:
    """
    Run the full 4-stage screening pipeline for one market.
    """
    conf = MARKETS[market]
    print(f"\n{'=' * 60}")
    print(f"  {conf['name']} ({market.upper()})")
    print(f"{'=' * 60}")

    # Step 1: Load universe
    symbols = load_universe(market)
    print(f"  Universe: {len(symbols)} symbols")

    # Apply suffix for column matching (data comes with .US/.HK from Longbridge)
    suffixed = [f"{s}{conf['yfinance_suffix']}" if not s.endswith(conf['yfinance_suffix']) else s
                for s in symbols]

    # Step 2: Fetch data (stocks + VIX + DXY in one batch)
    prices = fetch_price_data(symbols, market, force_refresh=force_refresh)
    print(f"  Price data: {prices.shape}")

    # Steps 3-5: Run pipeline (Stages 1-3) — use suffixed symbols for column matching
    liq = conf["liquidity"]
    pipeline = run_stage2_and_3(
        prices, suffixed,
        min_price=liq["min_price"],
        min_adv=liq["min_adv_dollars"],
    )

    # Assemble output
    groups_output = {}
    for gid, gdata in pipeline.get("groups", {}).items():
        label = gdata["label"]
        long_candidates = [s for s in gdata["stocks"] if s.get("candidate_type") == "long"]
        short_candidates = [s for s in gdata["stocks"] if s.get("candidate_type") == "short"]

        # Trim to top_n per group
        long_candidates = sorted(long_candidates, key=lambda x: x["rs_zscore"], reverse=True)[:top_n]
        short_candidates = sorted(short_candidates, key=lambda x: x["rs_zscore"])[:top_n]

        groups_output[label] = {
            "n_stocks": gdata["n_stocks"],
            "avg_beta_vix": gdata["avg_beta_vix"],
            "avg_beta_dxy": gdata["avg_beta_dxy"],
            "long_candidates": long_candidates,
            "short_candidates": short_candidates,
        }

    # Print summary
    print(f"\n  ── Watchlist Summary ──")
    for label, g in groups_output.items():
        print(f"    {label}: {g['n_stocks']} stocks, "
              f"beta_vix={g['avg_beta_vix']:+.3f}, "
              f"long={len(g['long_candidates'])} short={len(g['short_candidates'])}")

    # Print top long candidates across all groups
    print(f"\n  ── Top Long Candidates ──")
    all_long = []
    for g in groups_output.values():
        all_long.extend(g["long_candidates"])
    all_long = sorted(all_long, key=lambda x: x["rs_zscore"], reverse=True)[:10]
    for s in all_long:
        print(f"    {s['symbol']:>8s}  beta_vix={s['beta_vix']:+.3f}  rs_z={s['rs_zscore']:+.3f}")

    # Print top short candidates across all groups
    print(f"\n  ── Top Short Candidates ──")
    all_short = []
    for g in groups_output.values():
        all_short.extend(g["short_candidates"])
    all_short = sorted(all_short, key=lambda x: x["rs_zscore"])[:10]
    for s in all_short:
        print(f"    {s['symbol']:>8s}  beta_vix={s['beta_vix']:+.3f}  rs_z={s['rs_zscore']:+.3f}")

    return {
        "market": market,
        "liquidity_passed": pipeline["liquidity_passed"],
        "total_in_universe": pipeline["total_in_universe"],
        "groups": groups_output,
    }


def main():
    parser = argparse.ArgumentParser(
        description="ATS Watchlist Screener — 4-stage quantitative pipeline"
    )
    parser.add_argument(
        "--markets",
        default="us",
        help="Markets to screen (comma-separated: us, hk). Default: us",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Max candidates per group per side. Default: 20",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force clear all cached data before running",
    )
    args = parser.parse_args()

    markets = [m.strip() for m in args.markets.split(",")]

    if args.refresh:
        print("Clearing all cached data...")
        invalidate_cache()
        print("Done.\n")

    t0 = time.time()
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    for market in markets:
        try:
            result = screen_market(
                market=market,
                top_n=args.top,
                force_refresh=args.refresh,
            )
            results[market] = result
        except Exception as e:
            print(f"\n  ERROR screening {market}: {e}")
            import traceback
            traceback.print_exc()
            results[market] = {"market": market, "error": str(e)}

    # Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(results, indent=2, cls=NumpyEncoder))
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"  Watchlist written to {OUTPUT_FILE}")
    print(f"  Total time: {elapsed:.1f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
