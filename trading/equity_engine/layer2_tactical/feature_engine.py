"""
Feature engineering for the XGBoost entry classifier.

Computes features from M15 bar data for real-time inference.
Feature set matches what the training script produces — do NOT
modify the featur list without retraining the model.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Feature names — must match training exactly
FEATURE_NAMES = [
    "rsi_14",
    "atr_pct",
    "vwap_distance_pct",
    "volume_zscore",
    "sma200_distance_pct",
    "macd_hist",
    "bb_pct_b",
    "m15_momentum_pct",
    "volume_ratio",
]


@dataclass
class FeatureVector:
    """Computed feature values for one symbol at one point in time."""
    symbol: str
    rsi_14: float = 50.0
    atr_pct: float = 0.0
    vwap_distance_pct: float = 0.0
    volume_zscore: float = 0.0
    sma200_distance_pct: float = 0.0
    macd_hist: float = 0.0
    bb_pct_b: float = 0.5
    m15_momentum_pct: float = 0.0
    volume_ratio: float = 1.0

    def to_array(self) -> np.ndarray:
        """Return features as a numpy array in the correct order for XGBoost."""
        return np.array([
            self.rsi_14,
            self.atr_pct,
            self.vwap_distance_pct,
            self.volume_zscore,
            self.sma200_distance_pct,
            self.macd_hist,
            self.bb_pct_b,
            self.m15_momentum_pct,
            self.volume_ratio,
        ], dtype=np.float32)

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in FEATURE_NAMES}


class FeatureEngine:
    """
    Computes M15 features from bar data for XGBoost inference.

    Stateless — takes bar arrays, returns features.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        atr_period: int = 14,
        volume_z_period: int = 20,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ):
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.volume_z_period = volume_z_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.bb_period = bb_period
        self.bb_std = bb_std

    def compute(
        self,
        symbol: str,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        volumes: np.ndarray,
        sma200: Optional[float] = None,
        vwap: Optional[float] = None,
    ) -> FeatureVector:
        """
        Compute all features for one symbol.

        Args:
            symbol: Ticker string
            closes: M15 close prices (most recent last)
            highs: M15 high prices
            lows: M15 low prices
            volumes: M15 volume
            sma200: D1 SMA(200) value (from Layer 1)
            vwap: Session VWAP (cumulative volume-weighted average price)

        Returns:
            FeatureVector with all 9 features populated
        """
        fv = FeatureVector(symbol=symbol)

        if len(closes) < max(self.rsi_period, self.atr_period, self.volume_z_period,
                              self.macd_slow + self.macd_signal, self.bb_period) + 1:
            logger.debug(f"{symbol}: insufficient bars for features ({len(closes)})")
            return fv

        price = closes[-1]
        if price <= 0:
            return fv

        # 1. RSI(14)
        fv.rsi_14 = self._compute_rsi(closes, self.rsi_period)

        # 2. ATR as % of price
        atr_val = self._compute_atr(highs, lows, closes, self.atr_period)
        fv.atr_pct = atr_val / price

        # 3. VWAP distance
        if vwap and vwap > 0:
            fv.vwap_distance_pct = (price - vwap) / vwap
        else:
            # Estimate VWAP from available bars
            est_vwap = self._estimate_vwap(closes, highs, lows, volumes)
            if est_vwap > 0:
                fv.vwap_distance_pct = (price - est_vwap) / est_vwap

        # 4. Volume Z-score
        fv.volume_zscore = self._compute_volume_zscore(volumes, self.volume_z_period)

        # 5. SMA(200) distance
        if sma200 and sma200 > 0:
            fv.sma200_distance_pct = (price - sma200) / sma200

        # 6. MACD histogram
        fv.macd_hist = self._compute_macd_hist(
            closes, self.macd_fast, self.macd_slow, self.macd_signal,
        )

        # 7. Bollinger %B
        fv.bb_pct_b = self._compute_bb_pct_b(closes, self.bb_period, self.bb_std)

        # 8. M15 momentum (5-bar returns)
        if len(closes) >= 6:
            fv.m15_momentum_pct = (closes[-1] - closes[-6]) / closes[-6] if closes[-6] > 0 else 0

        # 9. Volume ratio (current vs 20-bar avg)
        if len(volumes) >= self.volume_z_period:
            avg_vol = np.mean(volumes[-self.volume_z_period:])
            fv.volume_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

        return fv

    # ── Indicator helpers ──────────────────────────────────────────────

    @staticmethod
    def _compute_rsi(closes: np.ndarray, period: int) -> float:
        """RSI using Wilder's smoothing."""
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        if avg_loss == 0:
            return 100.0

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        return float(100.0 - (100.0 / (1.0 + rs)))

    @staticmethod
    def _compute_atr(highs, lows, closes, period: int) -> float:
        """Wilder's ATR."""
        if len(closes) < period + 1:
            return float(np.mean(highs - lows))
        prev_close = closes[:-1]
        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - prev_close)
        tr3 = np.abs(lows[1:] - prev_close)
        tr = np.maximum(np.maximum(tr1, tr2), tr3)

        atr = float(np.mean(tr[:period]))
        for i in range(period, len(tr)):
            atr = (atr * (period - 1) + tr[i]) / period
        return atr

    @staticmethod
    def _compute_volume_zscore(volumes: np.ndarray, period: int) -> float:
        """Z-score of latest volume vs rolling mean/std."""
        if len(volumes) < period:
            return 0.0
        window = volumes[-period:]
        mean = np.mean(window)
        std = np.std(window)
        if std == 0:
            return 0.0
        return float((volumes[-1] - mean) / std)

    @staticmethod
    def _compute_macd_hist(closes: np.ndarray, fast: int, slow: int, signal: int) -> float:
        """MACD histogram value."""
        if len(closes) < slow + signal:
            return 0.0
        # EMAs
        ema_fast = FeatureEngine._ema(closes, fast)
        ema_slow = FeatureEngine._ema(closes, slow)
        macd_line = ema_fast - ema_slow
        signal_line = FeatureEngine._ema_1d(macd_line, signal)
        return float(macd_line[-1] - signal_line[-1])

    @staticmethod
    def _compute_bb_pct_b(closes: np.ndarray, period: int, std_mult: float) -> float:
        """Bollinger %B = (price - lower) / (upper - lower)."""
        if len(closes) < period:
            return 0.5
        window = closes[-period:]
        mid = np.mean(window)
        std = np.std(window)
        upper = mid + std * std_mult
        lower = mid - std * std_mult
        if upper == lower:
            return 0.5
        return float((closes[-1] - lower) / (upper - lower))

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Exponential moving average over array."""
        alpha = 2 / (period + 1)
        result = np.zeros_like(data)
        result[0] = data[0]
        for i in range(1, len(data)):
            result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
        return result

    @staticmethod
    def _ema_1d(data: np.ndarray, period: int) -> np.ndarray:
        """EMA for 1D array (used on MACD line)."""
        return FeatureEngine._ema(data, period)

    @staticmethod
    def _estimate_vwap(closes, highs, lows, volumes) -> float:
        """Estimate VWAP from typical price × volume."""
        tp = (highs + lows + closes) / 3
        if volumes.sum() == 0:
            return 0.0
        return float(np.sum(tp * volumes) / np.sum(volumes))