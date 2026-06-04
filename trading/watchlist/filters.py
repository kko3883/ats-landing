"""
Tier 3 strategy-specific filters.

After the factor scoring (Tier 2) produces a ranked list of top stocks,
Tier 3 applies per-bucket strategy filters to identify which stocks are
currently actionable for each strategy:

  Bucket 2 (Alpha Gen):
    - Pullback: RSI < threshold + near 50-day SMA
    - Breakout: Price > N-day high + volume surge

  Bucket 3 (Convexity):
    - BB Squeeze: Bollinger Band width at multi-month low + volume expansion
    - Divergence: RSI making lower lows while price makes higher highs

These filters are NOT trading signals — they're flags on the watchlist that
say "this stock deserves attention" for the relevant strategy.
"""

import numpy as np
import pandas as pd

from .config import TIER3_FILTERS


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI for a price series."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period).mean()


def _bb(width: pd.Series, period: int, std: float) -> tuple:
    """Bollinger Bands: middle, upper, lower, width as % of middle."""
    middle = _sma(width, period)
    std_val = width.rolling(window=period).std()
    upper = middle + std_val * std
    lower = middle - std_val * std
    bb_width = (upper - lower) / middle * 100
    return middle, upper, lower, bb_width


# ── Pullback Filter (Bucket 2) ─────────────────────────────────────────────


def check_pullback(
    close: pd.Series,
    conf: dict | None = None,
) -> bool:
    """
    Is this stock in a pullback? RSI < threshold and price near 50-day SMA.

    Active in: Choppy regime
    Used by: Mag 7 pullback strategy (MSFT/GOOGL/AMZN)
    """
    c = conf or TIER3_FILTERS["pullback"]

    if len(close) < c["sma_period"] + 10:
        return False

    rsi_val = _rsi(close, c["rsi_period"]).iloc[-1]
    sma_50 = _sma(close, c["sma_period"]).iloc[-1]
    current_price = close.iloc[-1]

    if pd.isna(rsi_val) or pd.isna(sma_50):
        return False

    near_sma = abs(current_price / sma_50 - 1) <= c["sma_proximity_pct"]
    oversold = rsi_val <= c["rsi_max"]

    return oversold and near_sma


# ── Breakout Filter (Bucket 2) ──────────────────────────────────────────────


def check_breakout(
    close: pd.Series,
    volume: pd.Series,
    conf: dict | None = None,
) -> bool:
    """
    Is this stock breaking out? Price > N-day high with volume surge.

    Active in: Trending regime
    Used by: Donchian breakout (Turtle system)
    """
    c = conf or TIER3_FILTERS["breakout"]

    if len(close) < c["lookback_days"] + 5 or len(volume) < 25:
        return False

    high_n_day = close.rolling(window=c["lookback_days"]).max().iloc[-1]
    avg_vol = volume.tail(25).mean()

    if pd.isna(high_n_day) or pd.isna(avg_vol) or avg_vol == 0:
        return False

    price_breakout = close.iloc[-1] >= high_n_day
    vol_surge = volume.iloc[-1] >= avg_vol * c["volume_multiple"]

    return price_breakout and vol_surge


# ── BB Squeeze Filter (Bucket 3) ────────────────────────────────────────────


def check_bb_squeeze(
    close: pd.Series,
    volume: pd.Series,
    conf: dict | None = None,
) -> bool:
    """
    Is the stock in a Bollinger Band squeeze? BB width at multi-month low
    and volume expanding.

    Active in: Trending regime (anticipating volatility expansion)
    Used by: Bollinger Band Squeeze strategy
    """
    c = conf or TIER3_FILTERS["bb_squeeze"]

    if len(close) < c["bb_lookback"] or len(volume) < 25:
        return False

    # Compute BB width over the last N periods
    bb_period = c["bb_period"]
    bb_std = c["bb_std"]
    bb_widths = []
    for i in range(len(close) - c["bb_lookback"], len(close)):
        window = close.iloc[max(0, i - bb_period + 1):i + 1]
        if len(window) < bb_period:
            continue
        middle = window.mean()
        std = window.std()
        upper = middle + std * bb_std
        lower = middle - std * bb_std
        width = (upper - lower) / middle * 100
        bb_widths.append(width)

    if len(bb_widths) < 20:
        return False

    current_width = bb_widths[-1]
    min_width = min(bb_widths)
    avg_vol = volume.tail(25).mean()

    # Squeeze: current width near 6-month low
    at_squeeze = current_width <= min_width * 1.05  # Within 5% of the low
    vol_expanding = volume.iloc[-1] >= avg_vol * c["volume_multiple"]

    return at_squeeze and vol_expanding


# ── Divergence Filter (Bucket 3) ────────────────────────────────────────────


def check_divergence(
    close: pd.Series,
    conf: dict | None = None,
) -> bool:
    """
    Is there RSI-price divergence? RSI making bearish divergence (lower high)
    while price makes higher high, or bullish divergence (higher low) while price
    makes lower low.

    Active in: All non-crisis regimes
    Used by: Volume RSI divergence strategy
    """
    c = conf or TIER3_FILTERS["divergence"]

    if len(close) < c["lookback_days"] * 2 + c["rsi_period"]:
        return False

    rsi_vals = _rsi(close, c["rsi_period"])

    # Look at two halves of the lookback window
    mid = len(close) - c["lookback_days"] // 2
    first_half = close.iloc[mid - c["lookback_days"] // 2:mid]
    second_half = close.iloc[mid:]

    if len(first_half) < 3 or len(second_half) < 3:
        return False

    # Check for bearish divergence: price makes higher high, RSI makes lower high
    price_high_1 = first_half.max()
    price_high_2 = second_half.max()
    rsi_high_1 = rsi_vals.iloc[mid - c["lookback_days"] // 2:mid].max()
    rsi_high_2 = rsi_vals.iloc[mid:].max()

    bearish_div = (
        price_high_2 > price_high_1
        and rsi_high_2 < rsi_high_1
        and not pd.isna(price_high_1 + price_high_2 + rsi_high_1 + rsi_high_2)
    )

    # Check for bullish divergence: price makes lower low, RSI makes higher low
    price_low_1 = first_half.min()
    price_low_2 = second_half.min()
    rsi_low_1 = rsi_vals.iloc[mid - c["lookback_days"] // 2:mid].min()
    rsi_low_2 = rsi_vals.iloc[mid:].min()

    bullish_div = (
        price_low_2 < price_low_1
        and rsi_low_2 > rsi_low_1
        and not pd.isna(price_low_1 + price_low_2 + rsi_low_1 + rsi_low_2)
    )

    return bearish_div or bullish_div


# ── Batch: Apply All Filters to Top Stocks ─────────────────────────────────


def apply_tier3_filters(
    close_panel: pd.DataFrame,
    volume_panel: pd.DataFrame,
    top_symbols: list[str],
) -> dict:
    """
    Apply all Tier 3 strategy filters to a list of top-ranked symbols.

    Returns dict of {symbol: {filter_name: bool}}.
    """
    results = {}
    for sym in top_symbols:
        if sym not in close_panel.columns:
            continue

        close_series = close_panel[sym].dropna()
        vol_series = (
            volume_panel[sym].dropna()
            if sym in volume_panel.columns
            else pd.Series(dtype=float)
        )

        if len(close_series) < 60:
            continue

        results[sym] = {
            "pullback": check_pullback(close_series),
            "breakout": check_breakout(close_series, vol_series),
            "bb_squeeze": check_bb_squeeze(close_series, vol_series),
            "divergence": check_divergence(close_series),
        }

    return results
