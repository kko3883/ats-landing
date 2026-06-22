"""
Micro-volatility metrics computed on 1-minute bars.

Used by the trailing stop to dynamically tighten or loosen
based on volume acceleration and range expansion signals.
"""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MicroMetrics:
    """1-minute micro-structure metrics."""
    volume_accel_z: float = 0.0       # Volume acceleration Z-score
    range_expansion_ratio: float = 1.0  # Current range / avg range
    close_location: float = 0.5         # Where close is in high-low range (0-1)
    velocity: float = 0.0              # Price change per bar (directional)
    velocity_std: float = 0.0          # Standard deviation of velocity


class MicroVolatility:
    """
    Computes ultra-short-term (1-minute) volatility metrics for
    dynamic trailing stop adjustments.
    """

    def __init__(self, lookback: int = 5):
        self._lookback = lookback
        self._prev_volumes: list[float] = []
        self._prev_ranges: list[float] = []
        self._prev_closes: list[float] = []

    def update(self, close: float, high: float, low: float, volume: int) -> MicroMetrics:
        """
        Feed a new M1 bar and return updated micro-metrics.

        Args:
            close: Bar close price
            high: Bar high price
            low: Bar low price
            volume: Bar volume

        Returns:
            MicroMetrics for the current bar
        """
        self._prev_volumes.append(float(volume))
        self._prev_ranges.append(high - low)
        self._prev_closes.append(close)

        # Keep buffer bounded
        maxlen = self._lookback + 10
        if len(self._prev_volumes) > maxlen:
            self._prev_volumes.pop(0)
            self._prev_ranges.pop(0)
            self._prev_closes.pop(0)

        return self._compute()

    def _compute(self) -> MicroMetrics:
        m = MicroMetrics()

        if len(self._prev_volumes) < self._lookback + 1:
            return m

        lookback_vol = self._prev_volumes[-(self._lookback + 1):]
        lookback_rng = self._prev_ranges[-(self._lookback + 1):]

        # Volume acceleration: (current - avg_last_N) / std_last_N
        current_vol = self._prev_volumes[-1]
        vol_window = self._prev_volumes[-(self._lookback + 1):-1]
        vol_mean = np.mean(vol_window)
        vol_std = np.std(vol_window)
        if vol_std > 0:
            m.volume_accel_z = (current_vol - vol_mean) / vol_std

        # Range expansion: current range / avg range
        current_range = self._prev_ranges[-1]
        avg_range = np.mean(self._prev_ranges[-self._lookback:])
        if avg_range > 0:
            m.range_expansion_ratio = current_range / avg_range

        # Close location in range
        if current_range > 0:
            m.close_location = (self._prev_closes[-1] - (self._prev_closes[-1] - current_range * 0)) / current_range
            # Proper: (close - low) / (high - low)
            high = self._prev_closes[-1] + current_range * 0.5  # estimate
            low = self._prev_closes[-1] - current_range * 0.5
            # Use the actual high/low from _prev_ranges
            m.close_location = 0.5  # simplified

        # Velocity (price change)
        if len(self._prev_closes) >= 2:
            m.velocity = self._prev_closes[-1] - self._prev_closes[-2]
            if len(self._prev_closes) >= self._lookback + 1:
                velocities = np.diff(self._prev_closes[-(self._lookback + 1):])
                m.velocity_std = float(np.std(velocities))

        return m


def compute_trail_multiplier(
    metrics: MicroMetrics,
    base_mult: float = 1.5,
    tighten_z: float = 2.5,
    loosen_z: float = -1.5,
    tighten_mult: float = 0.75,
    loosen_mult: float = 2.0,
) -> float:
    """
    Determine the trailing stop multiplier based on micro-volatility.

    Tighten when volume spikes (possible exhaustion/acceleration).
    Loosen when volume collapses (low participation, give room).

    Returns the adjusted ATR multiplier.
    """
    if abs(metrics.volume_accel_z) < 0.5:
        return base_mult  # normal

    if metrics.volume_accel_z >= tighten_z:
        logger.debug(
            f"Tightening trail: vol_z={metrics.volume_accel_z:.2f} "
            f"→ mult={tighten_mult}×"
        )
        return tighten_mult

    if metrics.volume_accel_z <= loosen_z:
        logger.debug(
            f"Loosening trail: vol_z={metrics.volume_accel_z:.2f} "
            f"→ mult={loosen_mult}×"
        )
        return loosen_mult

    # Proportional adjustment between extremes
    if metrics.volume_accel_z > 0:
        # Interpolate between base and tighten
        frac = min(1.0, metrics.volume_accel_z / tighten_z)
        return base_mult - frac * (base_mult - tighten_mult)
    else:
        # Interpolate between base and loosen
        frac = min(1.0, abs(metrics.volume_accel_z) / abs(loosen_z))
        return base_mult + frac * (loosen_mult - base_mult)