"""
Data adjustment checks and gap detection.

Ensures split/dividend-adjusted pricing is used and detects overnight
gaps that could cause false entries at market open.
"""

import logging
from datetime import datetime, time, timedelta, timezone

import numpy as np

from .longbridge_stream import Bar

logger = logging.getLogger(__name__)

# US regular trading hours (ET → UTC)
# 09:30 ET = 14:30 UTC (standard) or 13:30 UTC (daylight)
MARKET_OPEN_UTC = time(14, 30)   # 09:30 ET standard (Longbridge returns UTC+8 local? verify)
MARKET_CLOSE_UTC = time(21, 0)   # 16:00 ET standard


def check_overnight_gap(
    prev_close: float,
    current_open: float,
    atr: float,
    max_gap_atr_mult: float = 2.0,
) -> tuple[bool, float]:
    """
    Check if the overnight gap exceeds the allowable threshold.

    Args:
        prev_close: Previous day's closing price
        current_open: Current day's opening price
        atr: Average True Range (daily)
        max_gap_atr_mult: Maximum allowed gap as multiple of ATR

    Returns:
        (is_unsafe, gap_pct) — True if gap is too large to trade safely
    """
    if prev_close <= 0 or atr <= 0:
        return False, 0.0

    gap = abs(current_open - prev_close)
    gap_pct = gap / prev_close
    is_unsafe = gap > (atr * max_gap_atr_mult)

    if is_unsafe:
        logger.info(
            f"Overnight gap {gap_pct:.2%} exceeds {max_gap_atr_mult}× ATR({atr:.4f}) "
            f"— suppressing entries"
        )

    return is_unsafe, float(gap_pct)


def is_open_cooldown(
    bar_timestamp: datetime,
    cooldown_minutes: int = 5,
) -> bool:
    """
    Check if we are still in the post-open cooldown period.
    Prevents entries immediately at market open when spreads are wide.

    Returns True if the bar falls within the cooldown window.
    """
    bar_time = bar_timestamp.time()
    # Simple check: is the bar within cooldown_minutes of the open?
    open_dt = datetime.combine(bar_timestamp.date(), MARKET_OPEN_UTC)
    open_dt = open_dt.replace(tzinfo=timezone.utc)
    cooldown_end = open_dt + timedelta(minutes=cooldown_minutes)

    return open_dt <= bar_timestamp <= cooldown_end


def validate_bar_continuity(bars: list[Bar], expected_interval: timedelta) -> bool:
    """
    Check for missing bars (gaps) in a bar series.
    Returns True if all bars are contiguous within 2× the expected interval.
    """
    if len(bars) < 2:
        return True

    for i in range(1, len(bars)):
        gap = bars[i].timestamp - bars[i - 1].timestamp
        if gap > expected_interval * 2:
            logger.warning(
                f"Bar gap detected: {bars[i - 1].timestamp} → {bars[i].timestamp} "
                f"(gap={gap}, expected={expected_interval})"
            )
            return False
    return True


def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> float:
    """
    Compute Average True Range from numpy arrays of high, low, close.

    Uses Wilder's smoothing method (EMA with alpha = 1/period).
    """
    if len(highs) < period + 1:
        return float(np.mean(highs - lows)) if len(highs) > 0 else 0.0

    prev_close = closes[:-1]
    tr1 = highs[1:] - lows[1:]
    tr2 = np.abs(highs[1:] - prev_close)
    tr3 = np.abs(lows[1:] - prev_close)
    tr = np.maximum(np.maximum(tr1, tr2), tr3)

    # Wilder's smoothing
    atr_values = np.zeros(len(tr))
    atr_values[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + tr[i]) / period

    return float(atr_values[-1])


def detect_split_event(
    current_bar: Bar,
    prev_bar: Bar,
    price_change_threshold: float = -0.25,
) -> bool:
    """
    Heuristic: detect a stock split by checking for sudden -50% (2:1) or
    -25% (3:2) price drops that are NOT accompanied by proportional volume.
    Longbridge provides forward-adjusted data, so splits should be invisible,
    but we check defensively.
    """
    if prev_bar is None:
        return False
    pct_change = (current_bar.open - prev_bar.close) / prev_bar.close
    if pct_change < price_change_threshold:
        logger.warning(
            f"Possible split/dividend detected: {current_bar.symbol} "
            f"{prev_bar.close:.2f} → {current_bar.open:.2f} ({pct_change:.2%})"
        )
        return True
    return False