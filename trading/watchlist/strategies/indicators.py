"""
Indicator functions — standalone, parameterized.
Each function takes a price Series + kwargs and returns a boolean or float.

To add a new indicator:
  1. Write a function here (pure Python, ~10 lines)
  2. Register it in registry.py
  3. Use it in config_strategies.yaml — done
"""

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def sma(close: pd.Series, period: int = 50) -> pd.Series:
    """Simple moving average."""
    return close.rolling(window=period).mean()


def ema(close: pd.Series, period: int = 20) -> pd.Series:
    """Exponential moving average."""
    return close.ewm(span=period, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def bb_width(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.Series:
    """Bollinger Band width as % of middle band."""
    middle = close.rolling(window=period).mean()
    sigma = close.rolling(window=period).std()
    upper = middle + sigma * std
    lower = middle - sigma * std
    return (upper - lower) / middle * 100


def rsi_lt(close: pd.Series, period: int = 14, threshold: float = 35.0) -> bool:
    """RSI below threshold (oversold check)."""
    rsi_vals = rsi(close, period)
    if rsi_vals.empty or pd.isna(rsi_vals.iloc[-1]):
        return False
    return float(rsi_vals.iloc[-1]) < threshold


def rsi_gt(close: pd.Series, period: int = 14, threshold: float = 55.0) -> bool:
    """RSI above threshold (overbought check)."""
    rsi_vals = rsi(close, period)
    if rsi_vals.empty or pd.isna(rsi_vals.iloc[-1]):
        return False
    return float(rsi_vals.iloc[-1]) > threshold


def price_above_sma(close: pd.Series, period: int = 50) -> bool:
    """Price above moving average."""
    avg = sma(close, period)
    if avg.empty or pd.isna(avg.iloc[-1]):
        return False
    return float(close.iloc[-1]) > float(avg.iloc[-1])


def price_below_sma(close: pd.Series, period: int = 50) -> bool:
    """Price below moving average."""
    avg = sma(close, period)
    if avg.empty or pd.isna(avg.iloc[-1]):
        return False
    return float(close.iloc[-1]) < float(avg.iloc[-1])


def near_sma(close: pd.Series, period: int = 50, pct: float = 0.03) -> bool:
    """Price within N% of SMA."""
    avg = sma(close, period)
    if avg.empty or pd.isna(avg.iloc[-1]):
        return False
    return abs(float(close.iloc[-1]) / float(avg.iloc[-1]) - 1) <= pct


def sma_crossover(close: pd.Series, fast: int = 20, slow: int = 50) -> bool:
    """Fast SMA crossed above slow SMA (golden cross)."""
    f = sma(close, fast)
    s = sma(close, slow)
    if len(f) < 3 or len(s) < 3:
        return False
    return bool(f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1])


def sma_crossunder(close: pd.Series, fast: int = 20, slow: int = 50) -> bool:
    """Fast SMA crossed below slow SMA (death cross)."""
    f = sma(close, fast)
    s = sma(close, slow)
    if len(f) < 3 or len(s) < 3:
        return False
    return bool(f.iloc[-2] >= s.iloc[-2] and f.iloc[-1] < s.iloc[-1])


def breakout(close: pd.Series, volume: pd.Series, lookback: int = 20,
             volume_multiple: float = 1.5) -> bool:
    """Price > N-day high with volume surge."""
    if len(close) < lookback + 5 or len(volume) < 25:
        return False
    high_n = close.rolling(window=lookback).max().iloc[-1]
    avg_vol = volume.tail(25).mean()
    if pd.isna(high_n) or pd.isna(avg_vol) or avg_vol == 0:
        return False
    return bool(close.iloc[-1] >= high_n and volume.iloc[-1] >= avg_vol * volume_multiple)


def pullback(close: pd.Series, volume: pd.Series,
             rsi_period: int = 14, rsi_threshold: float = 35.0,
             sma_period: int = 50, sma_proximity: float = 0.03) -> bool:
    """Oversold + near SMA (pullback setup)."""
    return rsi_lt(close, rsi_period, rsi_threshold) and near_sma(close, sma_period, sma_proximity)


def bb_squeeze(close: pd.Series, volume: pd.Series,
               bb_period: int = 20, lookback: int = 90,
               volume_multiple: float = 1.5) -> bool:
    """BB width at multi-month low + volume expansion."""
    if len(close) < lookback:
        return False
    widths = []
    for i in range(len(close) - lookback, len(close)):
        window = close.iloc[max(0, i - bb_period + 1):i + 1]
        if len(window) < bb_period:
            continue
        middle = window.mean()
        sigma = window.std()
        upper = middle + sigma * 2.0
        lower = middle - sigma * 2.0
        widths.append((upper - lower) / middle * 100)

    if len(widths) < 20:
        return False
    current_w = widths[-1]
    min_w = min(widths)
    avg_vol = volume.tail(25).mean()
    return bool(current_w <= min_w * 1.05 and volume.iloc[-1] >= avg_vol * volume_multiple)


def divergence(close: pd.Series, rsi_period: int = 14,
               lookback: int = 14) -> str:
    """Detect RSI-price divergence. Returns 'bullish', 'bearish', or 'none'."""
    rsi_vals = rsi(close, rsi_period)
    if len(close) < lookback * 2 + rsi_period:
        return "none"

    mid = len(close) - lookback // 2
    first_half = close.iloc[mid - lookback // 2:mid]
    second_half = close.iloc[mid:]

    if len(first_half) < 3 or len(second_half) < 3:
        return "none"

    # Bearish: price higher high, RSI lower high
    if (second_half.max() > first_half.max() and
            rsi_vals.iloc[mid:].max() < rsi_vals.iloc[mid - lookback // 2:mid].max()):
        return "bearish"

    # Bullish: price lower low, RSI higher low
    if (second_half.min() < first_half.min() and
            rsi_vals.iloc[mid:].min() > rsi_vals.iloc[mid - lookback // 2:mid].min()):
        return "bullish"

    return "none"
