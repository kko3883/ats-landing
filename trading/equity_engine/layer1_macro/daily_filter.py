"""
Daily macro filter — SMA(200) trend gatekeeper.

Runs once per day before market open:
  1. Fetches D1 bars for the candidate universe
  2. Computes SMA(200) for each symbol
  3. Filters out stocks where price < SMA(200)
  4. Outputs the "Approved Shortlist" for Layer 2 processing
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..data.adjustments import check_overnight_gap
from .regime_client import RegimeState

logger = logging.getLogger(__name__)


@dataclass
class MacroSignal:
    """Per-symbol Layer 1 evaluation result."""
    symbol: str
    approved: bool
    price: float = 0.0
    sma200: float = 0.0
    price_above_sma: bool = False
    atr14: float = 0.0
    gap_unsafe: bool = False
    gap_pct: float = 0.0
    reject_reason: str = ""


class DailyMacroFilter:
    """
    Daily SMA(200) filter + overnight gap check.

    Evaluates the candidate universe and returns only approved symbols.
    """

    def __init__(
        self,
        sma_period: int = 200,
        d1_lookback: int = 300,
        max_gap_atr_mult: float = 2.0,
    ):
        self._sma_period = sma_period
        self._d1_lookback = d1_lookback
        self._max_gap_atr_mult = max_gap_atr_mult
        self._last_shortlist: list[str] = []
        self._last_run: Optional[datetime] = None

    def evaluate(
        self,
        symbols: list[str],
        d1_data: dict[str, pd.DataFrame],
        regime: RegimeState,
    ) -> list[MacroSignal]:
        """
        Run the daily filter on all candidate symbols.

        Args:
            symbols: Candidate tickers (e.g., ["AAPL.US", "MSFT.US", ...])
            d1_data: {symbol: DataFrame with OHLCV columns (at least close, high, low)}
            regime: Current market regime (for gap sensitivity adjustments)

        Returns:
            List of MacroSignal — one per symbol.  Filter on .approved downstream.
        """
        results = []
        for sym in symbols:
            signal = self._evaluate_one(sym, d1_data.get(sym), regime)
            results.append(signal)

        approved = [s.symbol for s in results if s.approved]
        rejected = [s for s in results if not s.approved]

        self._last_shortlist = approved
        self._last_run = datetime.now(timezone.utc)

        logger.info(
            f"Daily filter complete: {len(approved)}/{len(results)} approved "
            f"({len(rejected)} rejected)"
        )
        for r in rejected:
            logger.debug(f"  Rejected: {r.symbol} — {r.reject_reason}")

        return results

    def _evaluate_one(
        self,
        symbol: str,
        df: Optional[pd.DataFrame],
        regime: RegimeState,
    ) -> MacroSignal:
        """Evaluate a single symbol."""
        signal = MacroSignal(symbol=symbol, approved=False)

        # No data → reject
        if df is None or df.empty:
            signal.reject_reason = "no data"
            return signal

        required_cols = {"close", "high", "low"}
        if not required_cols.issubset(set(col.lower() for col in df.columns)):
            signal.reject_reason = "missing OHLC columns"
            return signal

        # Normalize column names to lowercase
        df = df.rename(columns={c: c.lower() for c in df.columns})

        # Need at least sma_period bars
        if len(df) < self._sma_period:
            signal.reject_reason = f"insufficient bars ({len(df)} < {self._sma_period})"
            return signal

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        # SMA(200)
        sma200 = float(close.rolling(self._sma_period).mean().iloc[-1])
        price = float(close.iloc[-1])

        signal.price = price
        signal.sma200 = sma200

        if np.isnan(sma200) or sma200 <= 0:
            signal.reject_reason = "SMA(200) invalid"
            return signal

        signal.price_above_sma = price > sma200
        if not signal.price_above_sma:
            signal.reject_reason = f"price ({price:.2f}) < SMA200 ({sma200:.2f})"
            return signal

        # ATR(14) for gap check
        signal.atr14 = self._compute_atr(high, low, close, period=14)

        # Overnight gap check (compare today's open to yesterday's close)
        if len(close) >= 2:
            prev_close = float(close.iloc[-2])
            current_open = float(df["open"].iloc[-1]) if "open" in df.columns else price
            gap_adjusted = self._max_gap_atr_mult
            if regime.tighten_stops:
                gap_adjusted *= 0.75  # tighter gap tolerance in choppy/risk_off
            unsafe, gap_pct = check_overnight_gap(
                prev_close, current_open, signal.atr14, gap_adjusted,
            )
            signal.gap_unsafe = unsafe
            signal.gap_pct = gap_pct
            if unsafe:
                signal.reject_reason = f"overnight gap {gap_pct:.2%} > {gap_adjusted}× ATR"
                return signal

        # All checks passed
        signal.approved = True
        return signal

    @staticmethod
    def _compute_atr(high, low, close, period: int = 14) -> float:
        """Wilder's ATR from pandas Series."""
        if len(close) < period + 1:
            return 0.0
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
        val = float(atr.iloc[-1])
        return val if not np.isnan(val) else 0.0

    @property
    def approved_shortlist(self) -> list[str]:
        return list(self._last_shortlist)


def compute_sma(series: pd.Series, period: int) -> Optional[float]:
    """Compute simple moving average. Returns None if insufficient data."""
    if len(series) < period:
        return None
    sma = series.rolling(period).mean().iloc[-1]
    return float(sma) if not np.isnan(sma) else None