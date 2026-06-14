"""
Market configurations and macro factor settings for the 4-stage watchlist pipeline.

The pipeline:
  1. Liquidity guardrails (price, volume)
  2. Multi-factor macro betas (VIX, DXY, credit, yield curve) via rolling regression
  3. Cross-sectional relative strength within VIX-beta groups
  4. Flow/event overlay (earnings, options — deferred for free tier)

For HK: same pipeline structure, but beta interpretation differs.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent

# ── Macro Factor Symbols ────────────────────────────────────────────────────

# v2: 5 macro factors (was VIX+DXY only)
#   ^VIX       — fear gauge / volatility
#   DX-Y.NYB   — USD strength (DXY index proxy)
#   HYG        — high-yield credit ETF (credit risk appetite)
#   TLT        — long-duration Treasury ETF (flight-to-quality)
#   ^TNX       — 10Y Treasury yield (yield curve numerator)
#   ^IRX       — 13-week T-bill yield (yield curve denominator, proxy for 2Y)
MACRO_SYMBOLS = ["^VIX", "DX-Y.NYB", "HYG", "TLT", "^TNX", "^IRX"]

MACRO_LABELS = {
    "^VIX": "vix",
    "DX-Y.NYB": "dxy",
    "HYG": "hyg",
    "TLT": "tlt",
    "^TNX": "tnx",
    "^IRX": "irx",
}

# ── Market Definitions ──────────────────────────────────────────────────────

MARKETS = {
    "us": {
        "name": "United States",
        "universe_file": HERE / "unis" / "us_stocks.json",
        "currency": "USD",
        "yfinance_suffix": ".US",
        "liquidity": {
            "min_price": 5.0,
            "min_adv_dollars": 20_000_000,  # $20M daily dollar volume
        },
    },
    "hk": {
        "name": "Hong Kong",
        "universe_file": HERE / "unis" / "hk_stocks.json",
        "currency": "HKD",
        "yfinance_suffix": ".HK",
        "liquidity": {
            "min_price": 2.0,   # HK$2
            "min_adv_dollars": 10_000_000,  # HKD 10M (thinner)
        },
    },
}

# ── VIX Beta Group Definitions ──────────────────────────────────────────────
#
# Stocks are assigned to groups based on their VIX beta from a 124-day
# regression of stock returns ~ VIX_chg + DXY_chg.
#
# VIX beta interpretation:
#   Negative (e.g., -3) → crashes when VIX spikes → high-growth / risk-on
#   Positive (e.g., +1) → stable or rises when VIX spikes → defensive
#
# The regime detector activates different groups:
#   Trending/Risk-On  → Q1, Q2 (most negative VIX beta)
#   Choppy            → Q3, Q4 (moderate)
#   Crisis/Risk-Off   → Q5 long + Q1/Q2 short candidates

VIX_BETA_THRESHOLDS = [-1.5, -0.5, 0.0, 0.5]  # Quintile boundaries

VIX_BETA_GROUP_LABELS = {
    0: "high_beta_growth",   # Q1: beta_vix < -1.5  (most risk-on)
    1: "moderate_growth",     # Q2: -1.5 ≤ beta_vix < -0.5
    2: "neutral",             # Q3: -0.5 ≤ beta_vix < 0.0
    3: "moderate_defensive",  # Q4: 0.0 ≤ beta_vix < 0.5
    4: "defensive",           # Q5: beta_vix ≥ 0.5  (most risk-off)
}

# ── Cross-Sectional RS Settings ─────────────────────────────────────────────

RS_LOOKBACK_DAYS = 21        # ~1 month relative strength
RS_TOP_PCT = 0.10            # Top 10% per group = long candidates
RS_BOTTOM_PCT = 0.10         # Bottom 10% per group = short candidates

# ── Output ──────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path.home() / ".hermes" / "trading"
OUTPUT_FILE = OUTPUT_DIR / "watchlist.json"


def load_universe(market: str) -> list[str]:
    """Load stock symbols from the universe file for a given market."""
    conf = MARKETS.get(market)
    if not conf:
        raise ValueError(f"Unknown market: {market}")

    with open(conf["universe_file"]) as f:
        data = json.load(f)

    if market == "us":
        symbols = data.get("stocks", [])
    else:
        symbols = data.get("all_sectors", [])

    return symbols
